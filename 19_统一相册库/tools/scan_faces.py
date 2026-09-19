#!/usr/bin/env python3
"""统一相册库 人脸扫描补标签。

对 library.db 中「尚未检测」的图片做人脸检测 + mbf/r50 双嵌入，
再用照片人物判定/ 下六人的 r50 kNN 模型打人物标签，写入：
  - faces(face_id, lib, content_key, face_idx, x,y,w,h, det_score, cluster, person, emb_mbf, emb_r50)
  - photo_person_tags(content_key, person, source='scan', score)

设计要点：
  - 检测与嵌入流程与 08/_face_index/build_index.py 完全一致（YuNet + buffalo mbf/r50），嵌入空间可比。
  - 只检测 is_primary=1 的主副本；重复副本因 content_key 相同，标签天然共享。
  - 人脸裁图不落盘（避免几十万小文件），只存嵌入与坐标。
  - 断点续跑：已出现在 faces 中的 content_key 自动跳过。

用法:
  python tools/scan_faces.py                # 全量（约 1.2 万张）
  python tools/scan_faces.py --lib 09       # 只扫 09
  python tools/scan_faces.py --limit 200    # 小样本验证
"""
import os
import sys
import time
import json
import sqlite3
import argparse

os.nice(10)
import cv2
import numpy as np
import onnxruntime as ort

ROOT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
DB = f"{ROOT}/19_统一相册库/library.db"
MODELS = f"{ROOT}/07_相册/相册&视频备份/_photo_index/models"
DET_MODEL = f"{MODELS}/face_detection_yunet_2023mar.onnx"
MBF_MODEL = f"{MODELS}/buffalo_s/w600k_mbf.onnx"
R50_MODEL = f"{MODELS}/buffalo_l/w600k_r50.onnx"

IMG_EXT = {"jpg", "jpeg", "png", "heic", "webp", "bmp"}
MODEL_THRESH = {"妈妈": 0.55, "艳艳": 0.30, "我": 0.40, "爸爸": 0.35,
                "七月": 0.45, "乐仔": 0.55}
NEW_FACE_ID_BASE = 9_000_000
SCRFD_MODEL = os.path.expanduser(
    "~/.insightface/models/antelopev2/scrfd_10g_bnkps.onnx")


class SCRFDet:
    """SCRFD-10G 检测器（2026-09-19 横评结论：独有框 16/24 真脸 vs YuNet 3/24，
    YuNet 的多检主要是玩偶/插画/后脑勺误检）。接口与 YuNet 调用点对齐。"""

    def __init__(self, thresh=0.5, input_size=(640, 640)):
        from insightface.model_zoo.scrfd import SCRFD
        self.m = SCRFD(model_file=SCRFD_MODEL)
        self.m.prepare(ctx_id=-1, input_size=input_size, det_thresh=thresh)

    def setInputSize(self, size):
        # insightface 的 SCRFD 用固定 input_size，图片自行 resize 后进网络；
        # 这里保持调用点形状不变（外层已把图压到 max_side，无需再变）。
        pass

    def detect(self, img):
        """返回与 YuNet API 相同的 (retval, faces)：
        faces 行 = [x, y, w, h, kps×10, score]，无脸时 faces=None。"""
        bboxes, kpss = self.m.detect(img, max_num=0, metric="max")
        if bboxes is None or len(bboxes) == 0:
            return 0, None
        out = []
        for i in range(bboxes.shape[0]):
            x1, y1, x2, y2, sc = bboxes[i]
            row = [float(x1), float(y1), float(x2 - x1), float(y2 - y1)]
            if kpss is not None:
                row.extend(float(v) for v in kpss[i].ravel())
            else:
                row.extend([0.0] * 10)
            row.append(float(sc))
            out.append(np.array(row, dtype=np.float32))
        return 1, out

ARC_DST = np.array([
    [38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
    [41.5493, 92.3655], [70.7299, 92.2041],
], dtype=np.float32)


def l2n(v):
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


def align_crop(img, landmarks):
    src = np.array(landmarks, dtype=np.float32)
    M, _ = cv2.estimateAffinePartial2D(src, ARC_DST, method=cv2.LMEDS)
    if M is None:
        return None
    return cv2.warpAffine(img, M, (112, 112), flags=cv2.INTER_LINEAR, borderValue=0)


def load_image(path):
    """读图：中文路径 + HEIC 兼容。返回 BGR ndarray 或 None。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".heic":
        try:
            import pillow_heif
            from PIL import Image
            pillow_heif.register_heif_opener()
            with Image.open(path) as im:
                im = im.convert("RGB")
                return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
        except Exception:
            return None
    buf = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def load_models():
    models = {}
    for p in ["妈妈", "艳艳", "我", "爸爸", "七月"]:
        m = json.load(open(f"{ROOT}/照片人物判定/{p}/{p}_model_v1.json", encoding="utf-8"))
        G = np.array([g["embedding"] for g in m["gallery"]], dtype=np.float32)
        models[p] = G / np.linalg.norm(G, axis=1, keepdims=True)
    m4 = json.load(open(f"{ROOT}/照片人物判定/乐仔/lezai_model_v4.json", encoding="utf-8"))
    G4 = np.array(m4["gallery"], dtype=np.float32)
    models["乐仔"] = G4 / np.linalg.norm(G4, axis=1, keepdims=True)  # v4 兼容 r50 空间
    return models


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib", default=None, help="只扫指定来源 07/08/09/18")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--det-thresh", type=float, default=0.6)
    ap.add_argument("--max-side", type=int, default=0,
                    help="检测前长边压缩；0=按检测器默认（yunet 640 / scrfd 1280）")
    ap.add_argument("--det", choices=["yunet", "scrfd"], default="yunet",
                    help="检测器（2026-09-19 横评：scrfd 独有框 16/24 真脸，yunet 多为误检）")
    args = ap.parse_args()
    if args.max_side == 0:
        args.max_side = 1280 if args.det == "scrfd" else 640

    t0 = time.time()
    db = sqlite3.connect(DB)
    done = {r[0] for r in db.execute("SELECT DISTINCT content_key FROM faces")}
    done |= {r[0] for r in db.execute("SELECT content_key FROM faces_scanned")}
    q = """SELECT content_key, lib, path FROM files
           WHERE is_primary=1 AND lower(ext) IN ('jpg','jpeg','png','heic','webp','bmp')"""
    params = []
    if args.lib:
        q += " AND lib=?"
        params.append(args.lib)
    q += " ORDER BY lib, path"
    todo = [(ck, lib, p) for ck, lib, p in db.execute(q, params)
            if ck not in done and os.path.exists(p)]
    if args.limit:
        todo = todo[:args.limit]
    print(f"待检测 {len(todo)} 张（已完成 {len(done)} 个内容）", file=sys.stderr)

    if args.det == "scrfd":
        det = SCRFDet(thresh=min(args.det_thresh, 0.5))
    else:
        det = cv2.FaceDetectorYN.create(DET_MODEL, "", (320, 320),
                                        score_threshold=args.det_thresh)
    so = ort.SessionOptions()
    so.intra_op_num_threads = 2
    so.inter_op_num_threads = 1
    mbf = ort.InferenceSession(MBF_MODEL, so, providers=["CPUExecutionProvider"])
    r50 = ort.InferenceSession(R50_MODEL, so, providers=["CPUExecutionProvider"])
    mbf_in = mbf.get_inputs()[0].name
    r50_in = r50.get_inputs()[0].name
    models = load_models()

    next_fid = db.execute("SELECT COALESCE(MAX(face_id),0)+1 FROM faces "
                          "WHERE face_id >= ?", (NEW_FACE_ID_BASE,)).fetchone()[0]
    if next_fid < NEW_FACE_ID_BASE:
        next_fid = NEW_FACE_ID_BASE

    n_f = n_no = n_err = n_tag = 0
    face_rows, tag_rows = [], []
    for i, (ck, lib, p) in enumerate(todo):
        try:
            img = load_image(p)
            if img is None:
                n_err += 1
                continue
            h, w = img.shape[:2]
            mx = max(h, w)
            scale = args.max_side / mx if mx > args.max_side else 1.0
            img_s = cv2.resize(img, (int(w * scale), int(h * scale))) if scale != 1.0 else img
            det.setInputSize((img_s.shape[1], img_s.shape[0]))
            _, faces = det.detect(img_s)
            if faces is None or len(faces) == 0:
                n_no += 1
                db.execute("INSERT OR IGNORE INTO faces_scanned VALUES(?)", (ck,))
                continue
            for fi, f in enumerate(faces):
                lms = f[4:14].reshape(5, 2) / scale
                x, y, fw, fh = [float(v) / scale for v in f[:4]]
                det_s = float(f[14])
                crop = align_crop(img, lms)
                if crop is None:
                    continue
                blob = cv2.dnn.blobFromImage(crop, 1.0 / 127.5, (112, 112),
                                             (127.5, 127.5, 127.5), swapRB=True)
                e1 = mbf.run(None, {mbf_in: blob.astype(np.float32)})[0][0]
                e1 = e1 / max(np.linalg.norm(e1), 1e-6)
                e2 = r50.run(None, {r50_in: blob.astype(np.float32)})[0][0]
                e2 = e2 / max(np.linalg.norm(e2), 1e-6)
                # 人物判定（r50 空间 kNN）
                person, score = None, None
                v = l2n(e2)
                best_p, best_s = None, -1.0
                for pn, G in models.items():
                    s = float((G @ v).max())
                    if s > best_s:
                        best_p, best_s = pn, s
                if best_p and best_s >= MODEL_THRESH[best_p]:
                    person, score = best_p, best_s
                    n_tag += 1
                face_rows.append((next_fid, lib, ck, fi, x, y, fw, fh, det_s,
                                  None, person,
                                  e1.astype(np.float32).tobytes(),
                                  e2.astype(np.float32).tobytes()))
                if person:
                    tag_rows.append((ck, person, "scan", score))
                next_fid += 1
                n_f += 1
            db.execute("INSERT OR IGNORE INTO faces_scanned VALUES(?)", (ck,))
        except Exception as e:
            n_err += 1
            print(f"  ⚠️ {os.path.basename(p)}: {e}", file=sys.stderr)

        if (i + 1) % 200 == 0:
            if face_rows:
                db.executemany("INSERT OR REPLACE INTO faces VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               face_rows)
                face_rows.clear()
            if tag_rows:
                db.executemany("INSERT OR REPLACE INTO photo_person_tags VALUES(?,?,?,?)",
                               tag_rows)
                tag_rows.clear()
            db.commit()
            el = time.time() - t0
            print(f"[{i+1}/{len(todo)}] 脸={n_f} 命中={n_tag} 无脸={n_no} 错={n_err} "
                  f"{el:.0f}s  预计剩余 {el/(i+1)*(len(todo)-i-1)/60:.1f}min", file=sys.stderr)

    if face_rows:
        db.executemany("INSERT OR REPLACE INTO faces VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", face_rows)
    if tag_rows:
        db.executemany("INSERT OR REPLACE INTO photo_person_tags VALUES(?,?,?,?)", tag_rows)
    db.commit()
    print(f"完成 {len(todo)} 张 -> {n_f} 张脸（人物命中 {n_tag}）, "
          f"无脸 {n_no}, 错 {n_err}, 用时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
