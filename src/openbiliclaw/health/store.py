"""健康管理系统存储层：SQLite 数据持久化。

独立管理健康相关数据表，提供 CRUD、检索、统计等底层操作。
表结构与项目主数据库共存，通过独立的 store 类访问。
表名统一使用 health_ 前缀，避免与其他模块冲突。
"""

from __future__ import annotations

import json
import sqlite3

from openbiliclaw.storage.database import open_db_conn
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .models import (
    Allergy,
    AllergyCreate,
    AllergyStatus,
    Appointment,
    AppointmentCreate,
    AppointmentStatus,
    AppointmentType,
    AppointmentUpdate,
    Condition,
    ConditionCreate,
    ConditionSeverity,
    ConditionStatus,
    ConditionUpdate,
    Doctor,
    DoctorCreate,
    DoctorUpdate,
    DocumentType,
    DrugInteraction,
    Encounter,
    EncounterCreate,
    EncounterPriority,
    EncounterType,
    EncounterUpdate,
    HealthDocument,
    HealthDocumentCreate,
    HealthDocumentUpdate,
    HealthInsight,
    HealthInsightCreate,
    HealthStats,
    Immunization,
    ImmunizationCreate,
    LabComponentStatus,
    LabResult,
    LabResultCreate,
    LabResultStatus,
    LabResultUpdate,
    LabTestComponent,
    LabTestComponentCreate,
    Medication,
    MedicationCreate,
    MedicationLog,
    MedicationLogCreate,
    MedicationLogStatus,
    MedicationStatus,
    MedicationType,
    MedicationUpdate,
    Patient,
    PatientCreate,
    PatientUpdate,
    Procedure,
    ProcedureCreate,
    ProcedureStatus,
    ProcedureType,
    ProcedureUpdate,
    TimelineEvent,
    VitalGlucoseContext,
    Vitals,
    VitalsCreate,
    AllergyUpdate,
)

if TYPE_CHECKING:
    from ..storage.database import Database


_SCHEMA_SQL = """
-- 患者档案
CREATE TABLE IF NOT EXISTS health_patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    gender TEXT DEFAULT '',
    birth_date TEXT,
    blood_type TEXT DEFAULT '',
    height_cm REAL,
    weight_kg REAL,
    phone TEXT DEFAULT '',
    relationship TEXT DEFAULT 'self',
    notes TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 就诊记录
CREATE TABLE IF NOT EXISTS health_encounters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    encounter_date TEXT NOT NULL,
    hospital TEXT DEFAULT '',
    department TEXT DEFAULT '',
    doctor TEXT DEFAULT '',
    encounter_type TEXT DEFAULT 'outpatient',
    priority TEXT DEFAULT 'routine',
    chief_complaint TEXT DEFAULT '',
    present_illness TEXT DEFAULT '',
    physical_exam TEXT DEFAULT '',
    diagnosis TEXT DEFAULT '',
    treatment_plan TEXT DEFAULT '',
    follow_up_instructions TEXT DEFAULT '',
    location TEXT DEFAULT '',
    cost_yuan REAL,
    notes TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES health_patients(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_health_encounters_patient ON health_encounters(patient_id);
CREATE INDEX IF NOT EXISTS idx_health_encounters_date ON health_encounters(encounter_date);
CREATE INDEX IF NOT EXISTS idx_health_encounters_type ON health_encounters(encounter_type);

-- 健康问题 / 疾病
CREATE TABLE IF NOT EXISTS health_conditions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    condition_name TEXT NOT NULL,
    diagnosis TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    severity TEXT,
    onset_date TEXT,
    resolved_date TEXT,
    icd10_code TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES health_patients(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_health_conditions_patient ON health_conditions(patient_id);
CREATE INDEX IF NOT EXISTS idx_health_conditions_status ON health_conditions(status);

-- 用药记录
CREATE TABLE IF NOT EXISTS health_medications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    medication_name TEXT NOT NULL,
    medication_type TEXT DEFAULT 'prescription',
    dosage TEXT DEFAULT '',
    frequency TEXT DEFAULT '',
    route TEXT DEFAULT '',
    indication TEXT DEFAULT '',
    start_date TEXT,
    end_date TEXT,
    status TEXT DEFAULT 'active',
    prescribing_doctor TEXT DEFAULT '',
    pharmacy TEXT DEFAULT '',
    side_effects TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES health_patients(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_health_medications_patient ON health_medications(patient_id);
CREATE INDEX IF NOT EXISTS idx_health_medications_status ON health_medications(status);

-- 化验结果主表
CREATE TABLE IF NOT EXISTS health_lab_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    encounter_id INTEGER,
    test_name TEXT NOT NULL,
    test_category TEXT DEFAULT '',
    facility TEXT DEFAULT '',
    status TEXT DEFAULT 'completed',
    ordered_date TEXT,
    completed_date TEXT,
    overall_interpretation TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES health_patients(id) ON DELETE CASCADE,
    FOREIGN KEY (encounter_id) REFERENCES health_encounters(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_health_lab_results_patient ON health_lab_results(patient_id);
CREATE INDEX IF NOT EXISTS idx_health_lab_results_date ON health_lab_results(completed_date);

-- 化验项目明细
CREATE TABLE IF NOT EXISTS health_lab_components (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lab_result_id INTEGER NOT NULL,
    test_name TEXT NOT NULL,
    abbreviation TEXT DEFAULT '',
    value REAL,
    unit TEXT DEFAULT '',
    qualitative_value TEXT DEFAULT '',
    ref_range_min REAL,
    ref_range_max REAL,
    ref_range_text TEXT DEFAULT '',
    status TEXT DEFAULT 'normal',
    category TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    FOREIGN KEY (lab_result_id) REFERENCES health_lab_results(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_health_lab_components_result ON health_lab_components(lab_result_id);
CREATE INDEX IF NOT EXISTS idx_health_lab_components_status ON health_lab_components(status);
CREATE INDEX IF NOT EXISTS idx_health_lab_components_name ON health_lab_components(test_name);

-- 检查 / 手术
CREATE TABLE IF NOT EXISTS health_procedures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    encounter_id INTEGER,
    procedure_name TEXT NOT NULL,
    procedure_type TEXT DEFAULT 'imaging',
    body_part TEXT DEFAULT '',
    facility TEXT DEFAULT '',
    doctor TEXT DEFAULT '',
    procedure_date TEXT NOT NULL,
    status TEXT DEFAULT 'completed',
    findings TEXT DEFAULT '',
    conclusion TEXT DEFAULT '',
    abnormal_summary TEXT DEFAULT '',
    follow_up_recommendation TEXT DEFAULT '',
    needs_follow_up INTEGER DEFAULT 0,
    notes TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES health_patients(id) ON DELETE CASCADE,
    FOREIGN KEY (encounter_id) REFERENCES health_encounters(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_health_procedures_patient ON health_procedures(patient_id);
CREATE INDEX IF NOT EXISTS idx_health_procedures_date ON health_procedures(procedure_date);
CREATE INDEX IF NOT EXISTS idx_health_procedures_type ON health_procedures(procedure_type);

-- 过敏史
CREATE TABLE IF NOT EXISTS health_allergies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    allergen TEXT NOT NULL,
    reaction TEXT DEFAULT '',
    severity TEXT DEFAULT 'mild',
    onset_date TEXT,
    status TEXT DEFAULT 'active',
    notes TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES health_patients(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_health_allergies_patient ON health_allergies(patient_id);

-- 生命体征
CREATE TABLE IF NOT EXISTS health_vitals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    recorded_date TEXT NOT NULL,
    systolic_bp INTEGER,
    diastolic_bp INTEGER,
    heart_rate INTEGER,
    temperature_c REAL,
    weight_kg REAL,
    height_cm REAL,
    oxygen_saturation REAL,
    respiratory_rate INTEGER,
    blood_glucose REAL,
    glucose_context TEXT,
    pain_scale INTEGER,
    notes TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES health_patients(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_health_vitals_patient ON health_vitals(patient_id);
CREATE INDEX IF NOT EXISTS idx_health_vitals_date ON health_vitals(recorded_date);

-- 疫苗接种
CREATE TABLE IF NOT EXISTS health_immunizations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    vaccine_name TEXT NOT NULL,
    date_administered TEXT NOT NULL,
    dose_number INTEGER,
    manufacturer TEXT DEFAULT '',
    lot_number TEXT DEFAULT '',
    site TEXT DEFAULT '',
    facility TEXT DEFAULT '',
    administering_person TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES health_patients(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_health_immunizations_patient ON health_immunizations(patient_id);
CREATE INDEX IF NOT EXISTS idx_health_immunizations_date ON health_immunizations(date_administered);

-- 医生 / 医疗机构
CREATE TABLE IF NOT EXISTS health_doctors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    title TEXT DEFAULT '',
    specialty TEXT DEFAULT '',
    hospital TEXT DEFAULT '',
    department TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    address TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_health_doctors_name ON health_doctors(name);
CREATE INDEX IF NOT EXISTS idx_health_doctors_specialty ON health_doctors(specialty);

-- 健康文档 / 附件（看病资料原件）
CREATE TABLE IF NOT EXISTS health_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    encounter_id INTEGER,
    title TEXT NOT NULL,
    document_type TEXT DEFAULT 'other',
    file_name TEXT DEFAULT '',
    file_path TEXT DEFAULT '',
    file_size INTEGER DEFAULT 0,
    mime_type TEXT DEFAULT '',
    document_date TEXT,
    hospital TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES health_patients(id) ON DELETE CASCADE,
    FOREIGN KEY (encounter_id) REFERENCES health_encounters(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_health_documents_patient ON health_documents(patient_id);
CREATE INDEX IF NOT EXISTS idx_health_documents_type ON health_documents(document_type);
CREATE INDEX IF NOT EXISTS idx_health_documents_date ON health_documents(document_date);

-- AI 健康洞察 / 报告解读
CREATE TABLE IF NOT EXISTS health_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    insight_type TEXT DEFAULT 'interpretation',
    content TEXT NOT NULL,
    model TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES health_patients(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_health_insights_patient ON health_insights(patient_id);
CREATE INDEX IF NOT EXISTS idx_health_insights_target ON health_insights(target_type, target_id);

-- 预约 / 复诊
CREATE TABLE IF NOT EXISTS health_appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    doctor_id INTEGER,
    title TEXT NOT NULL,
    appointment_type TEXT DEFAULT 'consultation',
    status TEXT DEFAULT 'scheduled',
    scheduled_date TEXT NOT NULL,
    scheduled_time TEXT DEFAULT '',
    hospital TEXT DEFAULT '',
    department TEXT DEFAULT '',
    doctor_name TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    reminder_enabled INTEGER DEFAULT 1,
    reminder_days_before INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES health_patients(id) ON DELETE CASCADE,
    FOREIGN KEY (doctor_id) REFERENCES health_doctors(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_health_appointments_patient ON health_appointments(patient_id);
CREATE INDEX IF NOT EXISTS idx_health_appointments_date ON health_appointments(scheduled_date);
CREATE INDEX IF NOT EXISTS idx_health_appointments_status ON health_appointments(status);

-- 服药记录 / 用药依从性
CREATE TABLE IF NOT EXISTS health_medication_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    medication_id INTEGER,
    medication_name TEXT NOT NULL,
    dosage TEXT DEFAULT '',
    scheduled_date TEXT NOT NULL,
    scheduled_time TEXT DEFAULT '',
    taken_at TEXT,
    status TEXT DEFAULT 'taken',
    notes TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES health_patients(id) ON DELETE CASCADE,
    FOREIGN KEY (medication_id) REFERENCES health_medications(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_health_medication_logs_patient ON health_medication_logs(patient_id);
CREATE INDEX IF NOT EXISTS idx_health_medication_logs_date ON health_medication_logs(scheduled_date);
CREATE INDEX IF NOT EXISTS idx_health_medication_logs_medication ON health_medication_logs(medication_id);
"""


def _parse_json(value: str | None, default: Any) -> Any:
    """安全解析 JSON 字段。"""
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def _dump_json(value: Any) -> str:
    """序列化 JSON 字段。"""
    return json.dumps(value, ensure_ascii=False)


def _parse_dt(value: str | None) -> datetime:
    """解析时间戳。"""
    if not value:
        return datetime.now()
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now()


class HealthStore:
    """健康管理数据存储。

    可以传入已有的 Database 实例复用连接，也可以传入独立路径。
    """

    def __init__(
        self,
        database: Database | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self._database = database
        self._db_path = Path(db_path) if db_path else None
        self._thread_local = threading.local()
        self._initialized = False

    @property
    def conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接。"""
        if self._database is not None:
            return self._database.conn
        if not hasattr(self._thread_local, "conn") or self._thread_local.conn is None:
            assert self._db_path is not None
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = open_db_conn(str(self._db_path))
            conn.execute("PRAGMA foreign_keys = ON")
            self._thread_local.conn = conn
        return self._thread_local.conn

    def initialize(self) -> None:
        """初始化数据库表结构。"""
        if self._initialized:
            return
        self.conn.executescript(_SCHEMA_SQL)
        self._migrate()
        self.conn.commit()
        self._initialized = True

    def _migrate(self) -> None:
        """执行数据库迁移，为旧表添加新列。"""
        # 为 health_patients 表添加紧急联系人列
        existing_cols = {
            row[1] for row in self.conn.execute("PRAGMA table_info(health_patients)").fetchall()
        }
        for col, col_def in [
            ("emergency_contact_name", "TEXT DEFAULT ''"),
            ("emergency_contact_phone", "TEXT DEFAULT ''"),
            ("emergency_contact_relation", "TEXT DEFAULT ''"),
        ]:
            if col not in existing_cols:
                self.conn.execute(f"ALTER TABLE health_patients ADD COLUMN {col} {col_def}")

    # ── 患者 ──────────────────────────────────────────────────

    def create_patient(self, data: PatientCreate) -> Patient:
        cur = self.conn.execute(
            """INSERT INTO health_patients
               (full_name, gender, birth_date, blood_type, height_cm, weight_kg, phone, relationship,
                emergency_contact_name, emergency_contact_phone, emergency_contact_relation, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.full_name,
                data.gender,
                data.birth_date,
                data.blood_type,
                data.height_cm,
                data.weight_kg,
                data.phone,
                data.relationship,
                data.emergency_contact_name,
                data.emergency_contact_phone,
                data.emergency_contact_relation,
                data.notes,
            ),
        )
        self.conn.commit()
        return self.get_patient(cur.lastrowid)

    def get_patient(self, patient_id: int) -> Patient:
        row = self.conn.execute(
            "SELECT * FROM health_patients WHERE id = ?", (patient_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"患者不存在: {patient_id}")
        return self._row_to_patient(row)

    def list_patients(self) -> list[Patient]:
        rows = self.conn.execute("SELECT * FROM health_patients ORDER BY id").fetchall()
        return [self._row_to_patient(r) for r in rows]

    def _row_to_patient(self, row: sqlite3.Row) -> Patient:
        # emergency_contact_* 列在旧库可能缺失，用 keys() 容错（sqlite3.Row 无 .get）
        _keys = set(row.keys())
        return Patient(
            id=row["id"],
            full_name=row["full_name"],
            gender=row["gender"],
            birth_date=row["birth_date"],
            blood_type=row["blood_type"],
            height_cm=row["height_cm"],
            weight_kg=row["weight_kg"],
            phone=row["phone"],
            relationship=row["relationship"],
            emergency_contact_name=row["emergency_contact_name"] if "emergency_contact_name" in _keys else "",
            emergency_contact_phone=row["emergency_contact_phone"] if "emergency_contact_phone" in _keys else "",
            emergency_contact_relation=row["emergency_contact_relation"] if "emergency_contact_relation" in _keys else "",
            notes=row["notes"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def update_patient(self, patient_id: int, data: PatientUpdate) -> Patient:
        updates = []
        params: list[Any] = []
        for field, value in data.model_dump(exclude_unset=True).items():
            updates.append(f"{field} = ?")
            params.append(value)
        if not updates:
            return self.get_patient(patient_id)
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(patient_id)
        self.conn.execute(
            f"UPDATE health_patients SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self.conn.commit()
        return self.get_patient(patient_id)

    def delete_patient(self, patient_id: int) -> None:
        self.conn.execute("DELETE FROM health_patients WHERE id = ?", (patient_id,))
        self.conn.commit()

    # ── 就诊记录 ──────────────────────────────────────────────

    def create_encounter(self, data: EncounterCreate) -> Encounter:
        cur = self.conn.execute(
            """INSERT INTO health_encounters
               (patient_id, encounter_date, hospital, department, doctor, encounter_type, priority,
                chief_complaint, present_illness, physical_exam, diagnosis, treatment_plan,
                follow_up_instructions, location, cost_yuan, notes, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.patient_id,
                data.encounter_date,
                data.hospital,
                data.department,
                data.doctor,
                data.encounter_type.value,
                data.priority.value,
                data.chief_complaint,
                data.present_illness,
                data.physical_exam,
                data.diagnosis,
                data.treatment_plan,
                data.follow_up_instructions,
                data.location,
                data.cost_yuan,
                data.notes,
                _dump_json(data.tags),
            ),
        )
        self.conn.commit()
        return self.get_encounter(cur.lastrowid)

    def get_encounter(self, encounter_id: int) -> Encounter:
        row = self.conn.execute(
            "SELECT * FROM health_encounters WHERE id = ?", (encounter_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"就诊记录不存在: {encounter_id}")
        return self._row_to_encounter(row)

    def _row_to_encounter(self, row: sqlite3.Row) -> Encounter:
        return Encounter(
            id=row["id"],
            patient_id=row["patient_id"],
            encounter_date=row["encounter_date"],
            hospital=row["hospital"],
            department=row["department"],
            doctor=row["doctor"],
            encounter_type=EncounterType(row["encounter_type"]),
            priority=EncounterPriority(row["priority"]),
            chief_complaint=row["chief_complaint"],
            present_illness=row["present_illness"],
            physical_exam=row["physical_exam"],
            diagnosis=row["diagnosis"],
            treatment_plan=row["treatment_plan"],
            follow_up_instructions=row["follow_up_instructions"],
            location=row["location"],
            cost_yuan=row["cost_yuan"],
            notes=row["notes"],
            tags=_parse_json(row["tags"], []),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def list_encounters(
        self,
        patient_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
        encounter_type: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        search: str | None = None,
    ) -> tuple[list[Encounter], int]:
        conditions = []
        params: list[Any] = []
        if patient_id is not None:
            conditions.append("patient_id = ?")
            params.append(patient_id)
        if encounter_type:
            conditions.append("encounter_type = ?")
            params.append(encounter_type)
        if start_date:
            conditions.append("encounter_date >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("encounter_date <= ?")
            params.append(end_date)
        if search:
            conditions.append(
                "(hospital LIKE ? OR department LIKE ? OR diagnosis LIKE ? OR chief_complaint LIKE ? OR doctor LIKE ?)"
            )
            like = f"%{search}%"
            params.extend([like, like, like, like, like])

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = self.conn.execute(
            f"SELECT COUNT(*) FROM health_encounters {where}", params
        ).fetchone()[0]

        rows = self.conn.execute(
            f"""SELECT * FROM health_encounters {where}
                ORDER BY encounter_date DESC, id DESC LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
        return [self._row_to_encounter(r) for r in rows], total

    def update_encounter(self, encounter_id: int, data: EncounterUpdate) -> Encounter:
        updates = []
        params: list[Any] = []
        for field, value in data.model_dump(exclude_unset=True).items():
            if field == "tags":
                updates.append("tags = ?")
                params.append(_dump_json(value))
            elif field in ("encounter_type", "priority") and value is not None:
                updates.append(f"{field} = ?")
                params.append(value.value)
            else:
                updates.append(f"{field} = ?")
                params.append(value)
        if not updates:
            return self.get_encounter(encounter_id)
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(encounter_id)
        self.conn.execute(
            f"UPDATE health_encounters SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self.conn.commit()
        return self.get_encounter(encounter_id)

    def delete_encounter(self, encounter_id: int) -> None:
        self.conn.execute("DELETE FROM health_encounters WHERE id = ?", (encounter_id,))
        self.conn.commit()

    # ── 健康问题 ──────────────────────────────────────────────

    def create_condition(self, data: ConditionCreate) -> Condition:
        cur = self.conn.execute(
            """INSERT INTO health_conditions
               (patient_id, condition_name, diagnosis, status, severity, onset_date, resolved_date, icd10_code, notes, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.patient_id,
                data.condition_name,
                data.diagnosis,
                data.status.value,
                data.severity.value if data.severity else None,
                data.onset_date,
                data.resolved_date,
                data.icd10_code,
                data.notes,
                _dump_json(data.tags),
            ),
        )
        self.conn.commit()
        return self.get_condition(cur.lastrowid)

    def get_condition(self, condition_id: int) -> Condition:
        row = self.conn.execute(
            "SELECT * FROM health_conditions WHERE id = ?", (condition_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"健康问题不存在: {condition_id}")
        return self._row_to_condition(row)

    def _row_to_condition(self, row: sqlite3.Row) -> Condition:
        return Condition(
            id=row["id"],
            patient_id=row["patient_id"],
            condition_name=row["condition_name"],
            diagnosis=row["diagnosis"],
            status=ConditionStatus(row["status"]),
            severity=ConditionSeverity(row["severity"]) if row["severity"] else None,
            onset_date=row["onset_date"],
            resolved_date=row["resolved_date"],
            icd10_code=row["icd10_code"],
            notes=row["notes"],
            tags=_parse_json(row["tags"], []),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def list_conditions(
        self,
        patient_id: int | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Condition], int]:
        conditions = []
        params: list[Any] = []
        if patient_id is not None:
            conditions.append("patient_id = ?")
            params.append(patient_id)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = self.conn.execute(
            f"SELECT COUNT(*) FROM health_conditions {where}", params
        ).fetchone()[0]
        rows = self.conn.execute(
            f"""SELECT * FROM health_conditions {where}
                ORDER BY status = 'active' DESC, onset_date DESC, id DESC LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
        return [self._row_to_condition(r) for r in rows], total

    def update_condition(self, condition_id: int, data: ConditionUpdate) -> Condition:
        updates = []
        params: list[Any] = []
        for field, value in data.model_dump(exclude_unset=True).items():
            if field == "tags":
                updates.append("tags = ?")
                params.append(_dump_json(value))
            elif field in ("status", "severity") and value is not None:
                updates.append(f"{field} = ?")
                params.append(value.value)
            else:
                updates.append(f"{field} = ?")
                params.append(value)
        if not updates:
            return self.get_condition(condition_id)
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(condition_id)
        self.conn.execute(
            f"UPDATE health_conditions SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self.conn.commit()
        return self.get_condition(condition_id)

    def delete_condition(self, condition_id: int) -> None:
        self.conn.execute("DELETE FROM health_conditions WHERE id = ?", (condition_id,))
        self.conn.commit()

    # ── 用药记录 ──────────────────────────────────────────────

    def create_medication(self, data: MedicationCreate) -> Medication:
        cur = self.conn.execute(
            """INSERT INTO health_medications
               (patient_id, medication_name, medication_type, dosage, frequency, route, indication,
                start_date, end_date, status, prescribing_doctor, pharmacy, side_effects, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.patient_id,
                data.medication_name,
                data.medication_type.value,
                data.dosage,
                data.frequency,
                data.route,
                data.indication,
                data.start_date,
                data.end_date,
                data.status.value,
                data.prescribing_doctor,
                data.pharmacy,
                data.side_effects,
                data.notes,
            ),
        )
        self.conn.commit()
        return self.get_medication(cur.lastrowid)

    def get_medication(self, medication_id: int) -> Medication:
        row = self.conn.execute(
            "SELECT * FROM health_medications WHERE id = ?", (medication_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"用药记录不存在: {medication_id}")
        return self._row_to_medication(row)

    def _row_to_medication(self, row: sqlite3.Row) -> Medication:
        return Medication(
            id=row["id"],
            patient_id=row["patient_id"],
            medication_name=row["medication_name"],
            medication_type=MedicationType(row["medication_type"]),
            dosage=row["dosage"],
            frequency=row["frequency"],
            route=row["route"],
            indication=row["indication"],
            start_date=row["start_date"],
            end_date=row["end_date"],
            status=MedicationStatus(row["status"]),
            prescribing_doctor=row["prescribing_doctor"],
            pharmacy=row["pharmacy"],
            side_effects=row["side_effects"],
            notes=row["notes"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def list_medications(
        self,
        patient_id: int | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Medication], int]:
        conditions = []
        params: list[Any] = []
        if patient_id is not None:
            conditions.append("patient_id = ?")
            params.append(patient_id)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = self.conn.execute(
            f"SELECT COUNT(*) FROM health_medications {where}", params
        ).fetchone()[0]
        rows = self.conn.execute(
            f"""SELECT * FROM health_medications {where}
                ORDER BY status = 'active' DESC, start_date DESC, id DESC LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
        return [self._row_to_medication(r) for r in rows], total

    def update_medication(self, medication_id: int, data: MedicationUpdate) -> Medication:
        updates = []
        params: list[Any] = []
        for field, value in data.model_dump(exclude_unset=True).items():
            if field in ("medication_type", "status") and value is not None:
                updates.append(f"{field} = ?")
                params.append(value.value)
            else:
                updates.append(f"{field} = ?")
                params.append(value)
        if not updates:
            return self.get_medication(medication_id)
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(medication_id)
        self.conn.execute(
            f"UPDATE health_medications SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self.conn.commit()
        return self.get_medication(medication_id)

    def delete_medication(self, medication_id: int) -> None:
        self.conn.execute("DELETE FROM health_medications WHERE id = ?", (medication_id,))
        self.conn.commit()

    # ── 化验结果 ──────────────────────────────────────────────

    def create_lab_result(self, data: LabResultCreate) -> LabResult:
        cur = self.conn.execute(
            """INSERT INTO health_lab_results
               (patient_id, encounter_id, test_name, test_category, facility, status,
                ordered_date, completed_date, overall_interpretation, notes, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.patient_id,
                data.encounter_id,
                data.test_name,
                data.test_category,
                data.facility,
                data.status.value,
                data.ordered_date,
                data.completed_date,
                data.overall_interpretation,
                data.notes,
                _dump_json(data.tags),
            ),
        )
        lab_result_id = cur.lastrowid
        for comp in data.components:
            self._create_component(lab_result_id, comp)
        self.conn.commit()
        return self.get_lab_result(lab_result_id)

    def _create_component(self, lab_result_id: int, data: LabTestComponentCreate) -> None:
        self.conn.execute(
            """INSERT INTO health_lab_components
               (lab_result_id, test_name, abbreviation, value, unit, qualitative_value,
                ref_range_min, ref_range_max, ref_range_text, status, category, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                lab_result_id,
                data.test_name,
                data.abbreviation,
                data.value,
                data.unit,
                data.qualitative_value,
                data.ref_range_min,
                data.ref_range_max,
                data.ref_range_text,
                data.status.value,
                data.category,
                data.notes,
            ),
        )

    def get_lab_result(self, lab_result_id: int) -> LabResult:
        row = self.conn.execute(
            "SELECT * FROM health_lab_results WHERE id = ?", (lab_result_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"化验结果不存在: {lab_result_id}")
        components = self._get_components(lab_result_id)
        return LabResult(
            id=row["id"],
            patient_id=row["patient_id"],
            encounter_id=row["encounter_id"],
            test_name=row["test_name"],
            test_category=row["test_category"],
            facility=row["facility"],
            status=LabResultStatus(row["status"]),
            ordered_date=row["ordered_date"],
            completed_date=row["completed_date"],
            overall_interpretation=row["overall_interpretation"],
            notes=row["notes"],
            tags=_parse_json(row["tags"], []),
            components=components,
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def _get_components(self, lab_result_id: int) -> list[LabTestComponent]:
        rows = self.conn.execute(
            "SELECT * FROM health_lab_components WHERE lab_result_id = ? ORDER BY id",
            (lab_result_id,),
        ).fetchall()
        return [
            LabTestComponent(
                id=r["id"],
                lab_result_id=r["lab_result_id"],
                test_name=r["test_name"],
                abbreviation=r["abbreviation"],
                value=r["value"],
                unit=r["unit"],
                qualitative_value=r["qualitative_value"],
                ref_range_min=r["ref_range_min"],
                ref_range_max=r["ref_range_max"],
                ref_range_text=r["ref_range_text"],
                status=LabComponentStatus(r["status"]),
                category=r["category"],
                notes=r["notes"],
            )
            for r in rows
        ]

    def list_lab_results(
        self,
        patient_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
    ) -> tuple[list[LabResult], int]:
        conditions = []
        params: list[Any] = []
        if patient_id is not None:
            conditions.append("patient_id = ?")
            params.append(patient_id)
        if search:
            conditions.append(
                "(test_name LIKE ? OR facility LIKE ? OR overall_interpretation LIKE ?)"
            )
            like = f"%{search}%"
            params.extend([like, like, like])
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = self.conn.execute(
            f"SELECT COUNT(*) FROM health_lab_results {where}", params
        ).fetchone()[0]
        rows = self.conn.execute(
            f"""SELECT * FROM health_lab_results {where}
                ORDER BY completed_date DESC, id DESC LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
        return [self.get_lab_result(r["id"]) for r in rows], total

    def update_lab_result(self, lab_result_id: int, data: LabResultUpdate) -> LabResult:
        updates = []
        params: list[Any] = []
        for field, value in data.model_dump(exclude_unset=True).items():
            if field == "tags":
                updates.append("tags = ?")
                params.append(_dump_json(value))
            elif field == "status" and value is not None:
                updates.append("status = ?")
                params.append(value.value)
            else:
                updates.append(f"{field} = ?")
                params.append(value)
        if not updates:
            return self.get_lab_result(lab_result_id)
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(lab_result_id)
        self.conn.execute(
            f"UPDATE health_lab_results SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self.conn.commit()
        return self.get_lab_result(lab_result_id)

    def delete_lab_result(self, lab_result_id: int) -> None:
        self.conn.execute("DELETE FROM health_lab_results WHERE id = ?", (lab_result_id,))
        self.conn.commit()

    # ── 检查 / 手术 ───────────────────────────────────────────

    def create_procedure(self, data: ProcedureCreate) -> Procedure:
        cur = self.conn.execute(
            """INSERT INTO health_procedures
               (patient_id, encounter_id, procedure_name, procedure_type, body_part, facility, doctor,
                procedure_date, status, findings, conclusion, abnormal_summary,
                follow_up_recommendation, needs_follow_up, notes, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.patient_id,
                data.encounter_id,
                data.procedure_name,
                data.procedure_type.value,
                data.body_part,
                data.facility,
                data.doctor,
                data.procedure_date,
                data.status.value,
                data.findings,
                data.conclusion,
                data.abnormal_summary,
                data.follow_up_recommendation,
                1 if data.needs_follow_up else 0,
                data.notes,
                _dump_json(data.tags),
            ),
        )
        self.conn.commit()
        return self.get_procedure(cur.lastrowid)

    def get_procedure(self, procedure_id: int) -> Procedure:
        row = self.conn.execute(
            "SELECT * FROM health_procedures WHERE id = ?", (procedure_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"检查记录不存在: {procedure_id}")
        return self._row_to_procedure(row)

    def _row_to_procedure(self, row: sqlite3.Row) -> Procedure:
        return Procedure(
            id=row["id"],
            patient_id=row["patient_id"],
            encounter_id=row["encounter_id"],
            procedure_name=row["procedure_name"],
            procedure_type=ProcedureType(row["procedure_type"]),
            body_part=row["body_part"],
            facility=row["facility"],
            doctor=row["doctor"],
            procedure_date=row["procedure_date"],
            status=ProcedureStatus(row["status"]),
            findings=row["findings"],
            conclusion=row["conclusion"],
            abnormal_summary=row["abnormal_summary"],
            follow_up_recommendation=row["follow_up_recommendation"],
            needs_follow_up=bool(row["needs_follow_up"]),
            notes=row["notes"],
            tags=_parse_json(row["tags"], []),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def list_procedures(
        self,
        patient_id: int | None = None,
        procedure_type: str | None = None,
        needs_follow_up: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Procedure], int]:
        conditions = []
        params: list[Any] = []
        if patient_id is not None:
            conditions.append("patient_id = ?")
            params.append(patient_id)
        if procedure_type:
            conditions.append("procedure_type = ?")
            params.append(procedure_type)
        if needs_follow_up is not None:
            conditions.append("needs_follow_up = ?")
            params.append(1 if needs_follow_up else 0)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = self.conn.execute(
            f"SELECT COUNT(*) FROM health_procedures {where}", params
        ).fetchone()[0]
        rows = self.conn.execute(
            f"""SELECT * FROM health_procedures {where}
                ORDER BY procedure_date DESC, id DESC LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
        return [self._row_to_procedure(r) for r in rows], total

    def update_procedure(self, procedure_id: int, data: ProcedureUpdate) -> Procedure:
        updates = []
        params: list[Any] = []
        for field, value in data.model_dump(exclude_unset=True).items():
            if field == "tags":
                updates.append("tags = ?")
                params.append(_dump_json(value))
            elif field == "needs_follow_up" and value is not None:
                updates.append("needs_follow_up = ?")
                params.append(1 if value else 0)
            elif field in ("procedure_type", "status") and value is not None:
                updates.append(f"{field} = ?")
                params.append(value.value)
            else:
                updates.append(f"{field} = ?")
                params.append(value)
        if not updates:
            return self.get_procedure(procedure_id)
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(procedure_id)
        self.conn.execute(
            f"UPDATE health_procedures SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self.conn.commit()
        return self.get_procedure(procedure_id)

    def delete_procedure(self, procedure_id: int) -> None:
        self.conn.execute("DELETE FROM health_procedures WHERE id = ?", (procedure_id,))
        self.conn.commit()

    # ── 过敏史 ────────────────────────────────────────────────

    def create_allergy(self, data: AllergyCreate) -> Allergy:
        cur = self.conn.execute(
            """INSERT INTO health_allergies
               (patient_id, allergen, reaction, severity, onset_date, status, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                data.patient_id,
                data.allergen,
                data.reaction,
                data.severity.value,
                data.onset_date,
                data.status.value,
                data.notes,
            ),
        )
        self.conn.commit()
        return self.get_allergy(cur.lastrowid)

    def get_allergy(self, allergy_id: int) -> Allergy:
        row = self.conn.execute(
            "SELECT * FROM health_allergies WHERE id = ?", (allergy_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"过敏记录不存在: {allergy_id}")
        return Allergy(
            id=row["id"],
            patient_id=row["patient_id"],
            allergen=row["allergen"],
            reaction=row["reaction"],
            severity=ConditionSeverity(row["severity"]),
            onset_date=row["onset_date"],
            status=AllergyStatus(row["status"]),
            notes=row["notes"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def list_allergies(self, patient_id: int | None = None) -> list[Allergy]:
        conditions = []
        params: list[Any] = []
        if patient_id is not None:
            conditions.append("patient_id = ?")
            params.append(patient_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.conn.execute(
            f"SELECT * FROM health_allergies {where} ORDER BY id", params
        ).fetchall()
        return [
            Allergy(
                id=r["id"],
                patient_id=r["patient_id"],
                allergen=r["allergen"],
                reaction=r["reaction"],
                severity=ConditionSeverity(r["severity"]),
                onset_date=r["onset_date"],
                status=AllergyStatus(r["status"]),
                notes=r["notes"],
                created_at=_parse_dt(r["created_at"]),
                updated_at=_parse_dt(r["updated_at"]),
            )
            for r in rows
        ]

    def delete_allergy(self, allergy_id: int) -> None:
        self.conn.execute("DELETE FROM health_allergies WHERE id = ?", (allergy_id,))
        self.conn.commit()

    # ── 生命体征 ──────────────────────────────────────────────

    def create_vitals(self, data: VitalsCreate) -> Vitals:
        cur = self.conn.execute(
            """INSERT INTO health_vitals
               (patient_id, recorded_date, systolic_bp, diastolic_bp, heart_rate, temperature_c,
                weight_kg, height_cm, oxygen_saturation, respiratory_rate, blood_glucose,
                glucose_context, pain_scale, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.patient_id,
                data.recorded_date,
                data.systolic_bp,
                data.diastolic_bp,
                data.heart_rate,
                data.temperature_c,
                data.weight_kg,
                data.height_cm,
                data.oxygen_saturation,
                data.respiratory_rate,
                data.blood_glucose,
                data.glucose_context.value if data.glucose_context else None,
                data.pain_scale,
                data.notes,
            ),
        )
        self.conn.commit()
        return self.get_vitals(cur.lastrowid)

    def get_vitals(self, vitals_id: int) -> Vitals:
        row = self.conn.execute("SELECT * FROM health_vitals WHERE id = ?", (vitals_id,)).fetchone()
        if row is None:
            raise ValueError(f"生命体征记录不存在: {vitals_id}")
        return Vitals(
            id=row["id"],
            patient_id=row["patient_id"],
            recorded_date=row["recorded_date"],
            systolic_bp=row["systolic_bp"],
            diastolic_bp=row["diastolic_bp"],
            heart_rate=row["heart_rate"],
            temperature_c=row["temperature_c"],
            weight_kg=row["weight_kg"],
            height_cm=row["height_cm"],
            oxygen_saturation=row["oxygen_saturation"],
            respiratory_rate=row["respiratory_rate"],
            blood_glucose=row["blood_glucose"],
            glucose_context=VitalGlucoseContext(row["glucose_context"])
            if row["glucose_context"]
            else None,
            pain_scale=row["pain_scale"],
            notes=row["notes"],
            created_at=_parse_dt(row["created_at"]),
        )

    def list_vitals(
        self, patient_id: int, limit: int = 100, offset: int = 0
    ) -> tuple[list[Vitals], int]:
        total = self.conn.execute(
            "SELECT COUNT(*) FROM health_vitals WHERE patient_id = ?", (patient_id,)
        ).fetchone()[0]
        rows = self.conn.execute(
            """SELECT * FROM health_vitals WHERE patient_id = ?
               ORDER BY recorded_date DESC, id DESC LIMIT ? OFFSET ?""",
            (patient_id, limit, offset),
        ).fetchall()
        return [self.get_vitals(r["id"]) for r in rows], total

    def delete_vitals(self, vitals_id: int) -> None:
        self.conn.execute("DELETE FROM health_vitals WHERE id = ?", (vitals_id,))
        self.conn.commit()

    # ── 疫苗接种 ──────────────────────────────────────────────

    def create_immunization(self, data: ImmunizationCreate) -> Immunization:
        cur = self.conn.execute(
            """INSERT INTO health_immunizations
               (patient_id, vaccine_name, date_administered, dose_number, manufacturer,
                lot_number, site, facility, administering_person, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.patient_id,
                data.vaccine_name,
                data.date_administered,
                data.dose_number,
                data.manufacturer,
                data.lot_number,
                data.site,
                data.facility,
                data.administering_person,
                data.notes,
            ),
        )
        self.conn.commit()
        return self.get_immunization(cur.lastrowid)

    def get_immunization(self, immunization_id: int) -> Immunization:
        row = self.conn.execute(
            "SELECT * FROM health_immunizations WHERE id = ?", (immunization_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"疫苗记录不存在: {immunization_id}")
        return Immunization(
            id=row["id"],
            patient_id=row["patient_id"],
            vaccine_name=row["vaccine_name"],
            date_administered=row["date_administered"],
            dose_number=row["dose_number"],
            manufacturer=row["manufacturer"],
            lot_number=row["lot_number"],
            site=row["site"],
            facility=row["facility"],
            administering_person=row["administering_person"],
            notes=row["notes"],
            created_at=_parse_dt(row["created_at"]),
        )

    def list_immunizations(self, patient_id: int | None = None) -> list[Immunization]:
        conditions = []
        params: list[Any] = []
        if patient_id is not None:
            conditions.append("patient_id = ?")
            params.append(patient_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.conn.execute(
            f"SELECT * FROM health_immunizations {where} ORDER BY date_administered DESC, id DESC",
            params,
        ).fetchall()
        return [
            Immunization(
                id=r["id"],
                patient_id=r["patient_id"],
                vaccine_name=r["vaccine_name"],
                date_administered=r["date_administered"],
                dose_number=r["dose_number"],
                manufacturer=r["manufacturer"],
                lot_number=r["lot_number"],
                site=r["site"],
                facility=r["facility"],
                administering_person=r["administering_person"],
                notes=r["notes"],
                created_at=_parse_dt(r["created_at"]),
            )
            for r in rows
        ]

    def delete_immunization(self, immunization_id: int) -> None:
        self.conn.execute("DELETE FROM health_immunizations WHERE id = ?", (immunization_id,))
        self.conn.commit()

    # ── 统计 ──────────────────────────────────────────────────

    def get_stats(self) -> HealthStats:
        def _count(table: str, where: str = "", params: tuple = ()) -> int:
            return self.conn.execute(f"SELECT COUNT(*) FROM {table} {where}", params).fetchone()[0]

        earliest = self.conn.execute(
            "SELECT MIN(encounter_date) FROM health_encounters"
        ).fetchone()[0]
        latest = self.conn.execute("SELECT MAX(encounter_date) FROM health_encounters").fetchone()[
            0
        ]

        return HealthStats(
            total_patients=_count("health_patients"),
            total_encounters=_count("health_encounters"),
            total_conditions=_count("health_conditions"),
            active_conditions=_count("health_conditions", "WHERE status = 'active'"),
            total_medications=_count("health_medications"),
            active_medications=_count("health_medications", "WHERE status = 'active'"),
            total_lab_results=_count("health_lab_results"),
            total_procedures=_count("health_procedures"),
            total_allergies=_count("health_allergies"),
            total_vitals=_count("health_vitals"),
            total_immunizations=_count("health_immunizations"),
            total_doctors=_count("health_doctors"),
            total_documents=_count("health_documents"),
            total_insights=_count("health_insights"),
            total_appointments=_count("health_appointments"),
            upcoming_appointments=_count(
                "health_appointments",
                "WHERE scheduled_date >= date('now') AND status IN ('scheduled', 'confirmed')",
            ),
            total_medication_logs=_count("health_medication_logs"),
            pending_follow_ups=_count(
                "health_procedures", "WHERE needs_follow_up = 1 AND status = 'completed'"
            ),
            earliest_encounter_date=earliest,
            latest_encounter_date=latest,
        )

    # ── 医生信息 ──────────────────────────────────────────────

    def create_doctor(self, data: DoctorCreate) -> Doctor:
        cur = self.conn.execute(
            """INSERT INTO health_doctors
               (name, title, specialty, hospital, department, phone, address, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.name,
                data.title,
                data.specialty,
                data.hospital,
                data.department,
                data.phone,
                data.address,
                data.notes,
            ),
        )
        self.conn.commit()
        return self.get_doctor(cur.lastrowid)

    def get_doctor(self, doctor_id: int) -> Doctor:
        row = self.conn.execute(
            "SELECT * FROM health_doctors WHERE id = ?", (doctor_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"医生不存在: {doctor_id}")
        return Doctor(
            id=row["id"],
            name=row["name"],
            title=row["title"],
            specialty=row["specialty"],
            hospital=row["hospital"],
            department=row["department"],
            phone=row["phone"],
            address=row["address"],
            notes=row["notes"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def list_doctors(self, specialty: str | None = None, search: str | None = None) -> list[Doctor]:
        conditions = []
        params: list[Any] = []
        if specialty:
            conditions.append("specialty = ?")
            params.append(specialty)
        if search:
            conditions.append("(name LIKE ? OR hospital LIKE ? OR specialty LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like])
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.conn.execute(
            f"SELECT * FROM health_doctors {where} ORDER BY name", params
        ).fetchall()
        return [self.get_doctor(r["id"]) for r in rows]

    def update_doctor(self, doctor_id: int, data: DoctorUpdate) -> Doctor:
        updates = []
        params: list[Any] = []
        for field, value in data.model_dump(exclude_unset=True).items():
            updates.append(f"{field} = ?")
            params.append(value)
        if not updates:
            return self.get_doctor(doctor_id)
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(doctor_id)
        self.conn.execute(f"UPDATE health_doctors SET {', '.join(updates)} WHERE id = ?", params)
        self.conn.commit()
        return self.get_doctor(doctor_id)

    def delete_doctor(self, doctor_id: int) -> None:
        self.conn.execute("DELETE FROM health_doctors WHERE id = ?", (doctor_id,))
        self.conn.commit()

    # ── 文档 / 附件 ────────────────────────────────────────────

    def create_document(self, data: HealthDocumentCreate) -> HealthDocument:
        cur = self.conn.execute(
            """INSERT INTO health_documents
               (patient_id, encounter_id, title, document_type, file_name, file_path,
                file_size, mime_type, document_date, hospital, summary, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.patient_id,
                data.encounter_id,
                data.title,
                data.document_type.value,
                data.file_name,
                data.file_path,
                data.file_size,
                data.mime_type,
                data.document_date,
                data.hospital,
                data.summary,
                _dump_json(data.tags),
            ),
        )
        self.conn.commit()
        return self.get_document(cur.lastrowid)

    def get_document(self, document_id: int) -> HealthDocument:
        row = self.conn.execute(
            "SELECT * FROM health_documents WHERE id = ?", (document_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"文档不存在: {document_id}")
        return HealthDocument(
            id=row["id"],
            patient_id=row["patient_id"],
            encounter_id=row["encounter_id"],
            title=row["title"],
            document_type=DocumentType(row["document_type"]),
            file_name=row["file_name"],
            file_path=row["file_path"],
            file_size=row["file_size"],
            mime_type=row["mime_type"],
            document_date=row["document_date"],
            hospital=row["hospital"],
            summary=row["summary"],
            tags=_parse_json(row["tags"], []),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def list_documents(
        self,
        patient_id: int | None = None,
        document_type: str | None = None,
        encounter_id: int | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[HealthDocument], int]:
        conditions = []
        params: list[Any] = []
        if patient_id is not None:
            conditions.append("patient_id = ?")
            params.append(patient_id)
        if document_type:
            conditions.append("document_type = ?")
            params.append(document_type)
        if encounter_id is not None:
            conditions.append("encounter_id = ?")
            params.append(encounter_id)
        if search:
            conditions.append("(title LIKE ? OR hospital LIKE ? OR summary LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like])
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = self.conn.execute(
            f"SELECT COUNT(*) FROM health_documents {where}", params
        ).fetchone()[0]
        rows = self.conn.execute(
            f"""SELECT * FROM health_documents {where}
                ORDER BY document_date DESC, id DESC LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
        return [self.get_document(r["id"]) for r in rows], total

    def update_document(self, document_id: int, data: HealthDocumentUpdate) -> HealthDocument:
        updates = []
        params: list[Any] = []
        for field, value in data.model_dump(exclude_unset=True).items():
            if field == "tags":
                updates.append("tags = ?")
                params.append(_dump_json(value))
            elif field == "document_type" and value is not None:
                updates.append("document_type = ?")
                params.append(value.value)
            else:
                updates.append(f"{field} = ?")
                params.append(value)
        if not updates:
            return self.get_document(document_id)
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(document_id)
        self.conn.execute(f"UPDATE health_documents SET {', '.join(updates)} WHERE id = ?", params)
        self.conn.commit()
        return self.get_document(document_id)

    def delete_document(self, document_id: int) -> None:
        self.conn.execute("DELETE FROM health_documents WHERE id = ?", (document_id,))
        self.conn.commit()

    # ── AI 健康洞察 ────────────────────────────────────────────

    def create_insight(self, data: HealthInsightCreate) -> HealthInsight:
        cur = self.conn.execute(
            """INSERT INTO health_insights
               (patient_id, target_type, target_id, insight_type, content, model)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                data.patient_id,
                data.target_type,
                data.target_id,
                data.insight_type,
                data.content,
                data.model,
            ),
        )
        self.conn.commit()
        return self.get_insight(cur.lastrowid)

    def get_insight(self, insight_id: int) -> HealthInsight:
        row = self.conn.execute(
            "SELECT * FROM health_insights WHERE id = ?", (insight_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"洞察不存在: {insight_id}")
        return HealthInsight(
            id=row["id"],
            patient_id=row["patient_id"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            insight_type=row["insight_type"],
            content=row["content"],
            model=row["model"],
            created_at=_parse_dt(row["created_at"]),
        )

    def list_insights(
        self,
        patient_id: int | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        limit: int = 50,
    ) -> list[HealthInsight]:
        conditions = []
        params: list[Any] = []
        if patient_id is not None:
            conditions.append("patient_id = ?")
            params.append(patient_id)
        if target_type:
            conditions.append("target_type = ?")
            params.append(target_type)
        if target_id is not None:
            conditions.append("target_id = ?")
            params.append(target_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.conn.execute(
            f"""SELECT * FROM health_insights {where}
                ORDER BY created_at DESC LIMIT ?""",
            [*params, limit],
        ).fetchall()
        return [self.get_insight(r["id"]) for r in rows]

    def delete_insight(self, insight_id: int) -> None:
        self.conn.execute("DELETE FROM health_insights WHERE id = ?", (insight_id,))
        self.conn.commit()

    # ── 健康时间线 ──────────────────────────────────────────────

    def get_timeline(
        self,
        patient_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TimelineEvent]:
        """获取患者的健康时间线，聚合所有类型的健康事件。"""
        events: list[TimelineEvent] = []

        # 就诊记录
        rows = self.conn.execute(
            """SELECT id, encounter_date as date, hospital, department, diagnosis, chief_complaint, encounter_type
               FROM health_encounters WHERE patient_id = ?
               ORDER BY encounter_date DESC LIMIT ? OFFSET ?""",
            (patient_id, limit, offset),
        ).fetchall()
        for r in rows:
            events.append(
                TimelineEvent(
                    id=r["id"],
                    date=r["date"],
                    event_type="encounter",
                    title=f"{r['hospital'] or ''} {r['department'] or ''}".strip() or "就诊",
                    description=r["diagnosis"] or r["chief_complaint"] or "",
                    status=r["encounter_type"],
                    severity="",
                    related_id=r["id"],
                )
            )

        # 检查记录
        rows = self.conn.execute(
            """SELECT id, procedure_date as date, procedure_name, conclusion, abnormal_summary, procedure_type, needs_follow_up
               FROM health_procedures WHERE patient_id = ?
               ORDER BY procedure_date DESC LIMIT ? OFFSET ?""",
            (patient_id, limit, offset),
        ).fetchall()
        for r in rows:
            events.append(
                TimelineEvent(
                    id=r["id"],
                    date=r["date"],
                    event_type="procedure",
                    title=r["procedure_name"],
                    description=r["conclusion"] or r["abnormal_summary"] or "",
                    status=r["procedure_type"],
                    severity="warning" if r["needs_follow_up"] else "",
                    related_id=r["id"],
                )
            )

        # 化验结果
        rows = self.conn.execute(
            """SELECT id, completed_date as date, test_name, overall_interpretation, facility
               FROM health_lab_results WHERE patient_id = ? AND completed_date IS NOT NULL
               ORDER BY completed_date DESC LIMIT ? OFFSET ?""",
            (patient_id, limit, offset),
        ).fetchall()
        for r in rows:
            # 检查是否有异常项
            abnormal = self.conn.execute(
                """SELECT COUNT(*) FROM health_lab_components
                   WHERE lab_result_id = ? AND status IN ('high','low','critical')""",
                (r["id"],),
            ).fetchone()[0]
            events.append(
                TimelineEvent(
                    id=r["id"],
                    date=r["date"] or "",
                    event_type="lab",
                    title=r["test_name"],
                    description=r["overall_interpretation"] or "",
                    status=f"{abnormal}项异常" if abnormal else "全部正常",
                    severity="warning" if abnormal else "",
                    related_id=r["id"],
                )
            )

        # 用药记录
        rows = self.conn.execute(
            """SELECT id, start_date as date, medication_name, dosage, frequency, status, indication
               FROM health_medications WHERE patient_id = ? AND start_date IS NOT NULL
               ORDER BY start_date DESC LIMIT ? OFFSET ?""",
            (patient_id, limit, offset),
        ).fetchall()
        for r in rows:
            events.append(
                TimelineEvent(
                    id=r["id"],
                    date=r["date"] or "",
                    event_type="medication",
                    title=r["medication_name"],
                    description=f"{r['dosage'] or ''} {r['frequency'] or ''}".strip(),
                    status=r["status"],
                    severity="",
                    related_id=r["id"],
                )
            )

        # 健康问题
        rows = self.conn.execute(
            """SELECT id, onset_date as date, condition_name, diagnosis, status, severity
               FROM health_conditions WHERE patient_id = ? AND onset_date IS NOT NULL
               ORDER BY onset_date DESC LIMIT ? OFFSET ?""",
            (patient_id, limit, offset),
        ).fetchall()
        for r in rows:
            events.append(
                TimelineEvent(
                    id=r["id"],
                    date=r["date"] or "",
                    event_type="condition",
                    title=r["condition_name"],
                    description=r["diagnosis"] or "",
                    status=r["status"],
                    severity=r["severity"] or "",
                    related_id=r["id"],
                )
            )

        # 文档
        rows = self.conn.execute(
            """SELECT id, document_date as date, title, hospital, document_type
               FROM health_documents WHERE patient_id = ? AND document_date IS NOT NULL
               ORDER BY document_date DESC LIMIT ? OFFSET ?""",
            (patient_id, limit, offset),
        ).fetchall()
        for r in rows:
            events.append(
                TimelineEvent(
                    id=r["id"],
                    date=r["date"] or "",
                    event_type="document",
                    title=r["title"],
                    description=r["hospital"] or "",
                    status=r["document_type"],
                    severity="",
                    related_id=r["id"],
                )
            )

        # 疫苗接种
        rows = self.conn.execute(
            """SELECT id, date_administered as date, vaccine_name, dose_number, facility
               FROM health_immunizations WHERE patient_id = ?
               ORDER BY date_administered DESC LIMIT ? OFFSET ?""",
            (patient_id, limit, offset),
        ).fetchall()
        for r in rows:
            events.append(
                TimelineEvent(
                    id=r["id"],
                    date=r["date"],
                    event_type="immunization",
                    title=r["vaccine_name"],
                    description=f"第{r['dose_number']}剂" if r["dose_number"] else "",
                    status=r["facility"] or "",
                    severity="",
                    related_id=r["id"],
                )
            )

        # 预约/复诊
        rows = self.conn.execute(
            """SELECT id, scheduled_date as date, title, hospital, department, status, appointment_type
               FROM health_appointments WHERE patient_id = ?
               ORDER BY scheduled_date DESC LIMIT ? OFFSET ?""",
            (patient_id, limit, offset),
        ).fetchall()
        for r in rows:
            events.append(
                TimelineEvent(
                    id=r["id"],
                    date=r["date"],
                    event_type="appointment",
                    title=r["title"],
                    description=f"{r['hospital'] or ''} {r['department'] or ''}".strip(),
                    status=r["status"],
                    severity="warning" if r["status"] == "scheduled" else "",
                    related_id=r["id"],
                )
            )

        # 按日期排序
        events.sort(key=lambda e: e.date or "", reverse=True)
        return events[:limit]

    # ── 预约 / 复诊 ────────────────────────────────────────────

    def create_appointment(self, data: AppointmentCreate) -> Appointment:
        cur = self.conn.execute(
            """INSERT INTO health_appointments
               (patient_id, doctor_id, title, appointment_type, status, scheduled_date,
                scheduled_time, hospital, department, doctor_name, reason, notes,
                reminder_enabled, reminder_days_before)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.patient_id,
                data.doctor_id,
                data.title,
                data.appointment_type.value,
                data.status.value,
                data.scheduled_date,
                data.scheduled_time,
                data.hospital,
                data.department,
                data.doctor_name,
                data.reason,
                data.notes,
                1 if data.reminder_enabled else 0,
                data.reminder_days_before,
            ),
        )
        self.conn.commit()
        return self.get_appointment(cur.lastrowid)

    def get_appointment(self, appointment_id: int) -> Appointment:
        row = self.conn.execute(
            "SELECT * FROM health_appointments WHERE id = ?", (appointment_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"预约不存在: {appointment_id}")
        return Appointment(
            id=row["id"],
            patient_id=row["patient_id"],
            doctor_id=row["doctor_id"],
            title=row["title"],
            appointment_type=AppointmentType(row["appointment_type"]),
            status=AppointmentStatus(row["status"]),
            scheduled_date=row["scheduled_date"],
            scheduled_time=row["scheduled_time"],
            hospital=row["hospital"],
            department=row["department"],
            doctor_name=row["doctor_name"],
            reason=row["reason"],
            notes=row["notes"],
            reminder_enabled=bool(row["reminder_enabled"]),
            reminder_days_before=row["reminder_days_before"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def list_appointments(
        self,
        patient_id: int | None = None,
        status: str | None = None,
        upcoming_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Appointment], int]:
        conditions = []
        params: list[Any] = []
        if patient_id is not None:
            conditions.append("patient_id = ?")
            params.append(patient_id)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if upcoming_only:
            conditions.append("scheduled_date >= date('now')")
            conditions.append("status IN ('scheduled', 'confirmed')")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = self.conn.execute(
            f"SELECT COUNT(*) FROM health_appointments {where}", params
        ).fetchone()[0]
        rows = self.conn.execute(
            f"""SELECT * FROM health_appointments {where}
                ORDER BY scheduled_date ASC, scheduled_time ASC LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
        return [self.get_appointment(r["id"]) for r in rows], total

    def update_appointment(self, appointment_id: int, data: AppointmentUpdate) -> Appointment:
        updates = []
        params: list[Any] = []
        for field, value in data.model_dump(exclude_unset=True).items():
            if field == "appointment_type" and value is not None:
                updates.append("appointment_type = ?")
                params.append(value.value)
            elif field == "status" and value is not None:
                updates.append("status = ?")
                params.append(value.value)
            elif field == "reminder_enabled":
                updates.append("reminder_enabled = ?")
                params.append(1 if value else 0)
            else:
                updates.append(f"{field} = ?")
                params.append(value)
        if not updates:
            return self.get_appointment(appointment_id)
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(appointment_id)
        self.conn.execute(
            f"UPDATE health_appointments SET {', '.join(updates)} WHERE id = ?", params
        )
        self.conn.commit()
        return self.get_appointment(appointment_id)

    def delete_appointment(self, appointment_id: int) -> None:
        self.conn.execute("DELETE FROM health_appointments WHERE id = ?", (appointment_id,))
        self.conn.commit()

    # ── 服药记录 / 用药依从性 ──────────────────────────────────

    def create_medication_log(self, data: MedicationLogCreate) -> MedicationLog:
        cur = self.conn.execute(
            """INSERT INTO health_medication_logs
               (patient_id, medication_id, medication_name, dosage, scheduled_date,
                scheduled_time, taken_at, status, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.patient_id,
                data.medication_id,
                data.medication_name,
                data.dosage,
                data.scheduled_date,
                data.scheduled_time,
                data.taken_at,
                data.status.value,
                data.notes,
            ),
        )
        self.conn.commit()
        return self.get_medication_log(cur.lastrowid)

    def get_medication_log(self, log_id: int) -> MedicationLog:
        row = self.conn.execute(
            "SELECT * FROM health_medication_logs WHERE id = ?", (log_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"服药记录不存在: {log_id}")
        return MedicationLog(
            id=row["id"],
            patient_id=row["patient_id"],
            medication_id=row["medication_id"],
            medication_name=row["medication_name"],
            dosage=row["dosage"],
            scheduled_date=row["scheduled_date"],
            scheduled_time=row["scheduled_time"],
            taken_at=row["taken_at"],
            status=MedicationLogStatus(row["status"]),
            notes=row["notes"],
            created_at=_parse_dt(row["created_at"]),
        )

    def list_medication_logs(
        self,
        patient_id: int | None = None,
        medication_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[MedicationLog], int]:
        conditions = []
        params: list[Any] = []
        if patient_id is not None:
            conditions.append("patient_id = ?")
            params.append(patient_id)
        if medication_id is not None:
            conditions.append("medication_id = ?")
            params.append(medication_id)
        if start_date:
            conditions.append("scheduled_date >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("scheduled_date <= ?")
            params.append(end_date)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = self.conn.execute(
            f"SELECT COUNT(*) FROM health_medication_logs {where}", params
        ).fetchone()[0]
        rows = self.conn.execute(
            f"""SELECT * FROM health_medication_logs {where}
                ORDER BY scheduled_date DESC, scheduled_time DESC LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
        return [self.get_medication_log(r["id"]) for r in rows], total

    def delete_medication_log(self, log_id: int) -> None:
        self.conn.execute("DELETE FROM health_medication_logs WHERE id = ?", (log_id,))
        self.conn.commit()

    def get_medication_adherence(self, patient_id: int, days: int = 30) -> dict:
        """计算用药依从率。"""
        end_date = self.conn.execute("SELECT date('now')").fetchone()[0]
        start_date = self.conn.execute(f"SELECT date('now', '-{days} days')").fetchone()[0]
        total = self.conn.execute(
            """SELECT COUNT(*) FROM health_medication_logs
               WHERE patient_id = ? AND scheduled_date BETWEEN ? AND ?""",
            (patient_id, start_date, end_date),
        ).fetchone()[0]
        taken = self.conn.execute(
            """SELECT COUNT(*) FROM health_medication_logs
               WHERE patient_id = ? AND scheduled_date BETWEEN ? AND ? AND status = 'taken'""",
            (patient_id, start_date, end_date),
        ).fetchone()[0]
        missed = self.conn.execute(
            """SELECT COUNT(*) FROM health_medication_logs
               WHERE patient_id = ? AND scheduled_date BETWEEN ? AND ? AND status = 'missed'""",
            (patient_id, start_date, end_date),
        ).fetchone()[0]
        rate = (taken / total * 100) if total > 0 else 0.0
        return {
            "period_days": days,
            "total_doses": total,
            "taken": taken,
            "missed": missed,
            "adherence_rate": round(rate, 1),
        }

    # ── 药物相互作用检查 ───────────────────────────────────────

    _KNOWN_DRUG_INTERACTIONS = [
        {
            "drugs": ["warfarin", "aspirin"],
            "severity": "major",
            "description": "阿司匹林显著增强华法林的抗凝作用，增加严重出血风险。",
        },
        {
            "drugs": ["warfarin", "ibuprofen"],
            "severity": "major",
            "description": "布洛芬等NSAIDs与华法林合用增加出血风险。",
        },
        {
            "drugs": ["warfarin", "naproxen"],
            "severity": "major",
            "description": "萘普生可能增强华法林的抗凝作用，增加出血风险。",
        },
        {
            "drugs": ["simvastatin", "clarithromycin"],
            "severity": "major",
            "description": "克拉霉素抑制CYP3A4，显著升高辛伐他汀水平，增加肌病风险。",
        },
        {
            "drugs": ["simvastatin", "erythromycin"],
            "severity": "major",
            "description": "红霉素抑制CYP3A4，增加辛伐他汀暴露和横纹肌溶解风险。",
        },
        {
            "drugs": ["metformin", "ibuprofen"],
            "severity": "moderate",
            "description": "NSAIDs可能降低肾功能，增加二甲双胍蓄积风险。",
        },
        {
            "drugs": ["lisinopril", "potassium"],
            "severity": "moderate",
            "description": "ACE抑制剂与钾补充剂合用可导致危险的高钾血症。",
        },
        {
            "drugs": ["methotrexate", "ibuprofen"],
            "severity": "major",
            "description": "NSAIDs降低甲氨蝶呤清除，可能导致严重毒性。",
        },
        {
            "drugs": ["clopidogrel", "omeprazole"],
            "severity": "moderate",
            "description": "奥美拉唑降低氯吡格雷活化，可能减弱其抗血小板作用。",
        },
        {
            "drugs": ["fluoxetine", "tramadol"],
            "severity": "major",
            "description": "合用增加5-羟色胺综合征风险，可能危及生命。",
        },
        {
            "drugs": ["sertraline", "tramadol"],
            "severity": "major",
            "description": "5-羟色胺能药物与曲马多合用有5-羟色胺综合征风险。",
        },
        {
            "drugs": ["digoxin", "amiodarone"],
            "severity": "major",
            "description": "胺碘酮显著升高地高辛血药浓度，有中毒风险。",
        },
        {
            "drugs": ["sildenafil", "nitrates"],
            "severity": "major",
            "description": "西地那非与硝酸盐合用导致严重低血压，禁忌。",
        },
        {
            "drugs": ["ciprofloxacin", "theophylline"],
            "severity": "major",
            "description": "环丙沙星抑制茶碱代谢，升高毒性风险。",
        },
        {
            "drugs": ["aspirin", "ibuprofen"],
            "severity": "moderate",
            "description": "布洛芬可能降低阿司匹林的抗血小板作用。",
        },
        {
            "drugs": ["amlodipine", "simvastatin"],
            "severity": "moderate",
            "description": "氨氯地平可能增加辛伐他汀暴露，需限制剂量。",
        },
        {
            "drugs": ["pinaverium", "anticholinergic"],
            "severity": "moderate",
            "description": "匹维溴铵与抗胆碱药合用可能增强抗胆碱作用。",
        },
    ]

    def check_drug_interactions(
        self, drug_name: str, existing_drugs: list[str]
    ) -> list[DrugInteraction]:
        """检查新药与现有药物的相互作用（静态已知危险对）。"""
        found = []
        drug_lower = drug_name.lower().strip()
        for pair in self._KNOWN_DRUG_INTERACTIONS:
            drug_a, drug_b = pair["drugs"]
            for existing in existing_drugs:
                ex_lower = existing.lower().strip()
                if len(drug_lower) < 4 or len(ex_lower) < 4:
                    match_a = drug_lower == drug_a and ex_lower == drug_b
                    match_b = drug_lower == drug_b and ex_lower == drug_a
                else:
                    match_a = (
                        drug_lower == drug_a
                        or drug_a.startswith(drug_lower)
                        or drug_lower.startswith(drug_a)
                    ) and (
                        ex_lower == drug_b
                        or drug_b.startswith(ex_lower)
                        or ex_lower.startswith(drug_b)
                    )
                    match_b = (
                        drug_lower == drug_b
                        or drug_b.startswith(drug_lower)
                        or drug_lower.startswith(drug_b)
                    ) and (
                        ex_lower == drug_a
                        or drug_a.startswith(ex_lower)
                        or ex_lower.startswith(drug_a)
                    )
                if match_a or match_b:
                    found.append(
                        DrugInteraction(
                            drug_a=drug_name,
                            drug_b=existing,
                            severity=pair["severity"],
                            description=pair["description"],
                        )
                    )
                    break
        return found
