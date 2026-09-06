"""导入用户真实看病数据到健康管理系统。

患者：童力，男，35岁
资料来源：用户上传的7张看病资料图片
"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保项目源码在路径中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from openbiliclaw.health import (
    AllergyCreate,
    ConditionCreate,
    ConditionSeverity,
    ConditionStatus,
    EncounterCreate,
    EncounterPriority,
    EncounterType,
    HealthService,
    ImmunizationCreate,
    LabComponentStatus,
    LabResultCreate,
    LabResultStatus,
    LabTestComponentCreate,
    MedicationCreate,
    MedicationStatus,
    MedicationType,
    PatientCreate,
    ProcedureCreate,
    ProcedureStatus,
    ProcedureType,
    VitalsCreate,
)

DB_PATH = PROJECT_ROOT / "data" / "openbiliclaw.db"


def main() -> None:
    svc = HealthService(db_path=str(DB_PATH))
    print(f"数据库: {DB_PATH}")

    # ── 1. 创建患者档案 ──
    patients = svc.list_patients()
    patient = None
    for p in patients:
        if p.full_name == "童力":
            patient = p
            break
    if patient is None:
        patient = svc.create_patient(
            PatientCreate(
                full_name="童力",
                gender="男",
                birth_date="1991-01-01",  # 35岁，约1991年生
                phone="13130494327",
                relationship="self",
                notes="登记号0009461102，病人ID 1659209",
            )
        )
        print(f"创建患者: {patient.full_name} (id={patient.id})")
    else:
        print(f"患者已存在: {patient.full_name} (id={patient.id})")

    pid = patient.id

    # ── 2. 就诊记录 ──

    # 2026-09-05 急诊
    encounter_emergency = svc.create_encounter(
        EncounterCreate(
            patient_id=pid,
            encounter_date="2026-09-05",
            hospital="深圳市宝安区中心医院",
            department="急诊科",
            encounter_type=EncounterType.EMERGENCY,
            priority=EncounterPriority.URGENT,
            chief_complaint="上腹痛伴恶心呕吐1.5小时",
            present_illness="患者1.5小时前无明显诱因出现上腹部疼痛，伴恶心呕吐，呕吐物为胃内容物。",
            physical_exam="生命体征正常，腹部平软，上腹部压痛，无反跳痛及肌紧张。",
            diagnosis="上腹痛待查：胆绞痛？胆总管结石？",
            treatment_plan="完善上腹部CT平扫、血常规、生化常规等检查；对症支持治疗。",
            notes="急诊就诊，开CT和化验检查",
            tags=["急诊", "腹痛", "胆石症"],
        )
    )
    print(f"创建急诊记录: id={encounter_emergency.id}")

    # 2026-09-06 门诊
    encounter_outpatient = svc.create_encounter(
        EncounterCreate(
            patient_id=pid,
            encounter_date="2026-09-06",
            hospital="深圳市宝安区中心医院",
            department="肝胆胰外科",
            encounter_type=EncounterType.OUTPATIENT,
            priority=EncounterPriority.ROUTINE,
            chief_complaint="发现胆总管结石1天",
            diagnosis="胆石症（胆总管结石、胆囊结石）",
            treatment_plan="1. 匹维溴铵片 50mg 口服 每日3次；2. 3天后门诊随诊；3. 必要时进一步检查治疗。",
            follow_up_instructions="3天后随诊，注意腹痛变化，如有加重及时就诊。",
            notes="门诊复诊，开具匹维溴铵",
            tags=["门诊", "肝胆胰外科", "胆石症"],
        )
    )
    print(f"创建门诊记录: id={encounter_outpatient.id}")

    # ── 3. 检查记录 ──

    # CT检查
    procedure_ct = svc.create_procedure(
        ProcedureCreate(
            patient_id=pid,
            encounter_id=encounter_emergency.id,
            procedure_name="上腹部CT平扫",
            procedure_type=ProcedureType.IMAGING,
            body_part="上腹部",
            facility="深圳市宝安区中心医院",
            procedure_date="2026-09-05",
            status=ProcedureStatus.COMPLETED,
            findings=(
                "肝脏形态大小正常，肝实质密度均匀。肝内胆管扩张，胆总管扩张，"
                "胆总管末端可见一大小约4×3×3mm高密度影，考虑结石。"
                "胆囊不大，壁不厚，腔内可见小结石影。"
                "胰腺、脾脏未见明显异常。"
            ),
            conclusion=(
                "1. 胆总管末端结石（4×3×3mm）伴肝内外胆管扩张（胆总管内径约9mm）；"
                "2. 胆囊小结石。"
            ),
            abnormal_summary="胆总管末端结石伴胆管扩张，胆囊小结石",
            follow_up_recommendation="建议肝胆胰外科就诊，必要时行ERCP或手术治疗。",
            needs_follow_up=True,
            notes="放射诊断报告单",
            tags=["CT", "胆总管结石", "胆囊结石"],
        )
    )
    print(f"创建CT检查记录: id={procedure_ct.id}")

    # 彩超检查
    procedure_us = svc.create_procedure(
        ProcedureCreate(
            patient_id=pid,
            encounter_id=encounter_outpatient.id,
            procedure_name="彩超肝胆胰脾",
            procedure_type=ProcedureType.IMAGING,
            body_part="肝胆胰脾",
            facility="深圳宝安中心医院",
            doctor="周杰辉",
            procedure_date="2026-09-06",
            status=ProcedureStatus.COMPLETED,
            findings=(
                "肝：形态大小正常，包膜光滑，肝实质回声均质；肝内血管走行自然，"
                "门静脉主干未见扩张。肝内外胆管未见扩张。"
                "胆囊切面体积不大，囊壁毛糙稍厚，附壁可见几个大小不等最大约4×3mm的"
                "异常高回声，呈颗粒状，后方无声影，改变体位不移动。"
                "肝内胆管扩张，局部内径4mm，胆总管扩张，上段内径9mm，中下段显示不清。"
                "胰腺：大小正常，实质内分布均匀；胰管未见扩张。"
                "脾：大小形态正常，实质分布均匀，未见异常回声。"
            ),
            conclusion=(
                "1. 胆囊壁隆起性病变，考虑胆囊息肉或胆囊附壁结石；"
                "2. 肝内、外胆管扩张，建议进一步检查。"
            ),
            abnormal_summary="胆囊息肉或附壁结石，肝内外胆管扩张",
            follow_up_recommendation="建议进一步检查，肝胆胰外科随诊。",
            needs_follow_up=True,
            notes="检查号10562168，申请/报告/审核医生：周杰辉，报告时间2026-09-06 12:08",
            tags=["彩超", "胆囊息肉", "胆管扩张"],
        )
    )
    print(f"创建彩超检查记录: id={procedure_us.id}")

    # ── 4. 化验结果 ──

    # 生化常规
    lab_biochem = svc.create_lab_result(
        LabResultCreate(
            patient_id=pid,
            encounter_id=encounter_emergency.id,
            test_name="生化常规",
            test_category="生化",
            facility="深圳市宝安区中心医院",
            status=LabResultStatus.COMPLETED,
            ordered_date="2026-09-05",
            completed_date="2026-09-05",
            overall_interpretation="葡萄糖6.7mmol/L偏高（参考3.9-6.1），肌酐108μmol/L偏高（参考64-104），余正常。尿酸351μmol/L在正常范围内偏高。",
            notes="急诊生化检查",
            tags=["生化", "血糖偏高", "肌酐偏高"],
            components=[
                LabTestComponentCreate(
                    test_name="葡萄糖", abbreviation="GLU", value=6.7, unit="mmol/L",
                    ref_range_min=3.9, ref_range_max=6.1, status=LabComponentStatus.HIGH,
                    category="生化",
                ),
                LabTestComponentCreate(
                    test_name="肌酐", abbreviation="CREA", value=108, unit="μmol/L",
                    ref_range_min=64, ref_range_max=104, status=LabComponentStatus.HIGH,
                    category="生化",
                ),
                LabTestComponentCreate(
                    test_name="尿酸", abbreviation="UA", value=351, unit="μmol/L",
                    ref_range_min=202, ref_range_max=416, status=LabComponentStatus.NORMAL,
                    category="生化", notes="正常范围内偏高",
                ),
                LabTestComponentCreate(
                    test_name="尿素氮", abbreviation="BUN", value=None, unit="mmol/L",
                    status=LabComponentStatus.NORMAL, category="生化",
                ),
                LabTestComponentCreate(
                    test_name="谷丙转氨酶", abbreviation="ALT", value=None, unit="U/L",
                    status=LabComponentStatus.NORMAL, category="肝功能",
                ),
                LabTestComponentCreate(
                    test_name="谷草转氨酶", abbreviation="AST", value=None, unit="U/L",
                    status=LabComponentStatus.NORMAL, category="肝功能",
                ),
                LabTestComponentCreate(
                    test_name="总胆红素", abbreviation="TBIL", value=None, unit="μmol/L",
                    status=LabComponentStatus.NORMAL, category="肝功能",
                ),
            ],
        )
    )
    print(f"创建生化化验记录: id={lab_biochem.id}")

    # 血常规+超敏C反应蛋白
    lab_cbc = svc.create_lab_result(
        LabResultCreate(
            patient_id=pid,
            encounter_id=encounter_emergency.id,
            test_name="血常规+超敏C反应蛋白",
            test_category="血常规",
            facility="深圳市宝安区中心医院",
            status=LabResultStatus.COMPLETED,
            ordered_date="2026-09-05",
            completed_date="2026-09-05",
            overall_interpretation="全部正常，无感染征象。",
            notes="急诊血常规检查",
            tags=["血常规", "全部正常"],
            components=[
                LabTestComponentCreate(
                    test_name="白细胞", abbreviation="WBC", value=None, unit="10^9/L",
                    ref_range_min=3.5, ref_range_max=9.5, status=LabComponentStatus.NORMAL,
                    category="血常规",
                ),
                LabTestComponentCreate(
                    test_name="中性粒细胞百分比", abbreviation="NEUT%", value=None, unit="%",
                    ref_range_min=40, ref_range_max=75, status=LabComponentStatus.NORMAL,
                    category="血常规",
                ),
                LabTestComponentCreate(
                    test_name="淋巴细胞百分比", abbreviation="LYMPH%", value=None, unit="%",
                    ref_range_min=20, ref_range_max=50, status=LabComponentStatus.NORMAL,
                    category="血常规",
                ),
                LabTestComponentCreate(
                    test_name="红细胞", abbreviation="RBC", value=None, unit="10^12/L",
                    ref_range_min=4.3, ref_range_max=5.8, status=LabComponentStatus.NORMAL,
                    category="血常规",
                ),
                LabTestComponentCreate(
                    test_name="血红蛋白", abbreviation="HGB", value=None, unit="g/L",
                    ref_range_min=130, ref_range_max=175, status=LabComponentStatus.NORMAL,
                    category="血常规",
                ),
                LabTestComponentCreate(
                    test_name="血小板", abbreviation="PLT", value=None, unit="10^9/L",
                    ref_range_min=125, ref_range_max=350, status=LabComponentStatus.NORMAL,
                    category="血常规",
                ),
                LabTestComponentCreate(
                    test_name="超敏C反应蛋白", abbreviation="hs-CRP", value=None, unit="mg/L",
                    ref_range_min=0, ref_range_max=5, status=LabComponentStatus.NORMAL,
                    category="炎症指标",
                ),
            ],
        )
    )
    print(f"创建血常规化验记录: id={lab_cbc.id}")

    # ── 5. 健康问题 ──

    conditions_data = [
        ConditionCreate(
            patient_id=pid,
            condition_name="胆总管结石",
            diagnosis="胆总管末端结石（4×3×3mm）伴肝内外胆管扩张（胆总管内径9mm）",
            status=ConditionStatus.ACTIVE,
            severity=ConditionSeverity.MODERATE,
            onset_date="2026-09-05",
            icd10_code="K80.3",
            notes="CT及彩超均提示胆总管扩张，末端结石。需肝胆胰外科随诊。",
            tags=["胆石症", "胆总管"],
        ),
        ConditionCreate(
            patient_id=pid,
            condition_name="胆囊结石/胆囊息肉",
            diagnosis="胆囊小结石；彩超提示胆囊壁隆起性病变，考虑胆囊息肉或胆囊附壁结石（最大4×3mm）",
            status=ConditionStatus.ACTIVE,
            severity=ConditionSeverity.MILD,
            onset_date="2026-09-05",
            icd10_code="K80.2",
            notes="CT提示胆囊小结石，彩超提示胆囊息肉或附壁结石。",
            tags=["胆石症", "胆囊", "胆囊息肉"],
        ),
        ConditionCreate(
            patient_id=pid,
            condition_name="胆绞痛",
            diagnosis="上腹痛伴恶心呕吐，考虑胆绞痛发作",
            status=ConditionStatus.ACTIVE,
            severity=ConditionSeverity.MODERATE,
            onset_date="2026-09-05",
            icd10_code="K80.5",
            notes="2026-09-05急诊因上腹痛伴恶心呕吐就诊。",
            tags=["腹痛", "胆绞痛"],
        ),
        ConditionCreate(
            patient_id=pid,
            condition_name="高尿酸血症",
            diagnosis="尿酸351μmol/L（参考202-416），正常范围内偏高；疾病诊断证明书诊断高尿酸血症",
            status=ConditionStatus.CHRONIC,
            severity=ConditionSeverity.MILD,
            onset_date="2026-09-05",
            icd10_code="E79.0",
            notes="需注意饮食，减少高嘌呤食物摄入，定期复查尿酸。",
            tags=["代谢", "尿酸"],
        ),
        ConditionCreate(
            patient_id=pid,
            condition_name="血糖偏高",
            diagnosis="空腹葡萄糖6.7mmol/L（参考3.9-6.1），高于正常范围",
            status=ConditionStatus.ACTIVE,
            severity=ConditionSeverity.MILD,
            onset_date="2026-09-05",
            notes="需复查空腹血糖及糖化血红蛋白，排除糖尿病前期。注意饮食控制。",
            tags=["代谢", "血糖"],
        ),
        ConditionCreate(
            patient_id=pid,
            condition_name="肌酐偏高",
            diagnosis="血肌酐108μmol/L（参考64-104），轻度升高",
            status=ConditionStatus.ACTIVE,
            severity=ConditionSeverity.MILD,
            onset_date="2026-09-05",
            notes="需复查肾功能，注意避免肾毒性药物，多饮水。",
            tags=["肾功能"],
        ),
    ]

    for cd in conditions_data:
        c = svc.create_condition(cd)
        print(f"创建健康问题: {c.condition_name} (id={c.id})")

    # ── 6. 用药记录 ──

    med = svc.create_medication(
        MedicationCreate(
            patient_id=pid,
            medication_name="匹维溴铵片",
            medication_type=MedicationType.PRESCRIPTION,
            dosage="50mg",
            frequency="每日3次",
            route="口服",
            indication="胆石症、胆绞痛",
            start_date="2026-09-06",
            status=MedicationStatus.ACTIVE,
            prescribing_doctor="肝胆胰外科",
            notes="50mg*15片/盒，每次1片，每日3次口服。用于对症缓解胆道功能紊乱相关的疼痛。",
        )
    )
    print(f"创建用药记录: {med.medication_name} (id={med.id})")

    # ── 7. 诊断证明书（作为备注记录在就诊中） ──
    # 疾病诊断证明书已包含在门诊就诊记录的diagnosis和treatment_plan中

    # ── 完成 ──
    stats = svc.get_stats()
    print("\n=== 导入完成 ===")
    print(f"患者: {stats.total_patients}")
    print(f"就诊记录: {stats.total_encounters}")
    print(f"健康问题: {stats.total_conditions} (活跃: {stats.active_conditions})")
    print(f"用药记录: {stats.total_medications} (活跃: {stats.active_medications})")
    print(f"化验结果: {stats.total_lab_results}")
    print(f"检查记录: {stats.total_procedures}")
    print(f"待复查: {stats.pending_follow_ups}")


if __name__ == "__main__":
    main()
