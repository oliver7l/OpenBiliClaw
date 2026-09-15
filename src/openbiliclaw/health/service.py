"""健康管理系统业务逻辑层。

封装存储层，提供患者档案管理、就诊记录、健康问题追踪、
用药管理、化验结果、检查记录、预约与服药依从性等高层业务接口。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .models import (
    Appointment,
    AppointmentCreate,
    AppointmentUpdate,
    Condition,
    ConditionCreate,
    ConditionUpdate,
    Doctor,
    DoctorCreate,
    DoctorUpdate,
    DrugInteraction,
    Encounter,
    EncounterCreate,
    EncounterUpdate,
    HealthDocument,
    HealthDocumentCreate,
    HealthDocumentUpdate,
    HealthStats,
    LabResult,
    LabResultCreate,
    LabResultUpdate,
    Medication,
    MedicationCreate,
    MedicationLog,
    MedicationLogCreate,
    MedicationUpdate,
    Patient,
    PatientCreate,
    PatientUpdate,
    Procedure,
    ProcedureCreate,
    ProcedureUpdate,
    TimelineEvent,
)
from .store import HealthStore

if TYPE_CHECKING:
    from ..storage.database import Database

logger = logging.getLogger(__name__)


def _format_ref_range(component: Any) -> str:
    """把化验明细的参考范围字段拼成可读文本。"""
    if component.ref_range_text:
        return component.ref_range_text
    lo, hi = component.ref_range_min, component.ref_range_max
    if lo is not None and hi is not None:
        return f"{lo}–{hi}"
    if lo is not None:
        return f"≥ {lo}"
    if hi is not None:
        return f"≤ {hi}"
    return "无"


class HealthService:
    """健康管理业务服务。

    封装存储层，提供高层业务接口。
    """

    def __init__(
        self,
        database: Database | None = None,
        db_path: str | None = None,
    ) -> None:
        self.store = HealthStore(database=database, db_path=db_path)
        self.store.initialize()

    # ── 患者档案 ──────────────────────────────────────────────

    def create_patient(self, data: PatientCreate) -> Patient:
        return self.store.create_patient(data)

    def get_patient(self, patient_id: int) -> Patient:
        return self.store.get_patient(patient_id)

    def list_patients(self) -> list[Patient]:
        return self.store.list_patients()

    def update_patient(self, patient_id: int, data: PatientUpdate) -> Patient:
        return self.store.update_patient(patient_id, data)

    def delete_patient(self, patient_id: int) -> None:
        self.store.delete_patient(patient_id)

    def get_or_create_self_patient(self) -> Patient:
        """获取或创建"本人"患者档案。"""
        patients = self.store.list_patients()
        for p in patients:
            if p.relationship == "self":
                return p
        return self.store.create_patient(PatientCreate(full_name="本人", relationship="self"))

    # ── 就诊记录 ──────────────────────────────────────────────

    def create_encounter(self, data: EncounterCreate) -> Encounter:
        return self.store.create_encounter(data)

    def get_encounter(self, encounter_id: int) -> Encounter:
        return self.store.get_encounter(encounter_id)

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
        return self.store.list_encounters(
            patient_id=patient_id,
            limit=limit,
            offset=offset,
            encounter_type=encounter_type,
            start_date=start_date,
            end_date=end_date,
            search=search,
        )

    def update_encounter(self, encounter_id: int, data: EncounterUpdate) -> Encounter:
        return self.store.update_encounter(encounter_id, data)

    def delete_encounter(self, encounter_id: int) -> None:
        self.store.delete_encounter(encounter_id)

    # ── 健康问题 ──────────────────────────────────────────────

    def create_condition(self, data: ConditionCreate) -> Condition:
        return self.store.create_condition(data)

    def get_condition(self, condition_id: int) -> Condition:
        return self.store.get_condition(condition_id)

    def list_conditions(
        self,
        patient_id: int | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Condition], int]:
        return self.store.list_conditions(
            patient_id=patient_id, status=status, limit=limit, offset=offset
        )

    def update_condition(self, condition_id: int, data: ConditionUpdate) -> Condition:
        return self.store.update_condition(condition_id, data)

    def delete_condition(self, condition_id: int) -> None:
        self.store.delete_condition(condition_id)

    # ── 用药记录 ──────────────────────────────────────────────

    def create_medication(self, data: MedicationCreate) -> Medication:
        return self.store.create_medication(data)

    def get_medication(self, medication_id: int) -> Medication:
        return self.store.get_medication(medication_id)

    def list_medications(
        self,
        patient_id: int | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Medication], int]:
        return self.store.list_medications(
            patient_id=patient_id, status=status, limit=limit, offset=offset
        )

    def update_medication(self, medication_id: int, data: MedicationUpdate) -> Medication:
        return self.store.update_medication(medication_id, data)

    def delete_medication(self, medication_id: int) -> None:
        self.store.delete_medication(medication_id)

    # ── 化验结果 ──────────────────────────────────────────────

    def create_lab_result(self, data: LabResultCreate) -> LabResult:
        return self.store.create_lab_result(data)

    def get_lab_result(self, lab_result_id: int) -> LabResult:
        return self.store.get_lab_result(lab_result_id)

    def list_lab_results(
        self,
        patient_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
    ) -> tuple[list[LabResult], int]:
        return self.store.list_lab_results(
            patient_id=patient_id, limit=limit, offset=offset, search=search
        )

    def update_lab_result(self, lab_result_id: int, data: LabResultUpdate) -> LabResult:
        return self.store.update_lab_result(lab_result_id, data)

    def delete_lab_result(self, lab_result_id: int) -> None:
        self.store.delete_lab_result(lab_result_id)

    def get_lab_trend(self, patient_id: int, test_name: str, limit: int = 50) -> list[dict]:
        """获取某个化验项目的历史趋势数据。

        从所有化验结果中筛选匹配 test_name 的项目，按日期排序返回。
        """
        results, _ = self.store.list_lab_results(patient_id=patient_id, limit=500)
        trend = []
        for lab in results:
            for comp in lab.components:
                if comp.test_name == test_name or comp.abbreviation == test_name:
                    trend.append(
                        {
                            "date": lab.completed_date or lab.ordered_date,
                            "value": comp.value,
                            "unit": comp.unit,
                            "status": comp.status.value,
                            "ref_range_min": comp.ref_range_min,
                            "ref_range_max": comp.ref_range_max,
                            "lab_result_id": lab.id,
                        }
                    )
        trend.sort(key=lambda x: x["date"] or "")
        return trend[:limit]

    # ── 检查 / 手术 ───────────────────────────────────────────

    def create_procedure(self, data: ProcedureCreate) -> Procedure:
        return self.store.create_procedure(data)

    def get_procedure(self, procedure_id: int) -> Procedure:
        return self.store.get_procedure(procedure_id)

    def list_procedures(
        self,
        patient_id: int | None = None,
        procedure_type: str | None = None,
        needs_follow_up: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Procedure], int]:
        return self.store.list_procedures(
            patient_id=patient_id,
            procedure_type=procedure_type,
            needs_follow_up=needs_follow_up,
            limit=limit,
            offset=offset,
        )

    def update_procedure(self, procedure_id: int, data: ProcedureUpdate) -> Procedure:
        return self.store.update_procedure(procedure_id, data)

    def delete_procedure(self, procedure_id: int) -> None:
        self.store.delete_procedure(procedure_id)

    # ── 统计与概览 ────────────────────────────────────────────

    def get_stats(self) -> HealthStats:
        return self.store.get_stats()

    def get_patient_summary(self, patient_id: int) -> dict:
        """获取患者完整档案摘要，包含各模块数量统计。"""
        patient = self.store.get_patient(patient_id)
        _, encounter_count = self.store.list_encounters(patient_id=patient_id, limit=1)
        _, condition_count = self.store.list_conditions(patient_id=patient_id, limit=1)
        active_conditions, _ = self.store.list_conditions(
            patient_id=patient_id, status="active", limit=100
        )
        _, medication_count = self.store.list_medications(patient_id=patient_id, limit=1)
        active_medications, _ = self.store.list_medications(
            patient_id=patient_id, status="active", limit=100
        )
        _, lab_count = self.store.list_lab_results(patient_id=patient_id, limit=1)
        _, procedure_count = self.store.list_procedures(patient_id=patient_id, limit=1)
        pending_procedures, _ = self.store.list_procedures(
            patient_id=patient_id, needs_follow_up=True, limit=100
        )

        _, doc_count = self.store.list_documents(patient_id=patient_id, limit=1)

        return {
            "patient": patient.model_dump(mode="json"),
            "counts": {
                "encounters": encounter_count,
                "conditions": condition_count,
                "active_conditions": len(active_conditions),
                "medications": medication_count,
                "active_medications": len(active_medications),
                "lab_results": lab_count,
                "procedures": procedure_count,
                "documents": doc_count,
                "pending_follow_ups": len(pending_procedures),
            },
            "active_conditions": [c.model_dump(mode="json") for c in active_conditions],
            "active_medications": [m.model_dump(mode="json") for m in active_medications],
            "pending_follow_ups": [p.model_dump(mode="json") for p in pending_procedures],
        }

    # ── 医生信息 ──────────────────────────────────────────────

    def create_doctor(self, data: DoctorCreate) -> Doctor:
        return self.store.create_doctor(data)

    def get_doctor(self, doctor_id: int) -> Doctor:
        return self.store.get_doctor(doctor_id)

    def list_doctors(self, specialty: str | None = None, search: str | None = None) -> list[Doctor]:
        return self.store.list_doctors(specialty=specialty, search=search)

    def update_doctor(self, doctor_id: int, data: DoctorUpdate) -> Doctor:
        return self.store.update_doctor(doctor_id, data)

    def delete_doctor(self, doctor_id: int) -> None:
        self.store.delete_doctor(doctor_id)

    # ── 文档 / 附件 ────────────────────────────────────────────

    def create_document(self, data: HealthDocumentCreate) -> HealthDocument:
        return self.store.create_document(data)

    def get_document(self, document_id: int) -> HealthDocument:
        return self.store.get_document(document_id)

    def list_documents(
        self,
        patient_id: int | None = None,
        document_type: str | None = None,
        encounter_id: int | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[HealthDocument], int]:
        return self.store.list_documents(
            patient_id=patient_id,
            document_type=document_type,
            encounter_id=encounter_id,
            search=search,
            limit=limit,
            offset=offset,
        )

    def update_document(self, document_id: int, data: HealthDocumentUpdate) -> HealthDocument:
        return self.store.update_document(document_id, data)

    def delete_document(self, document_id: int) -> None:
        self.store.delete_document(document_id)

    # ── 健康时间线 ──────────────────────────────────────────────

    def get_timeline(
        self, patient_id: int, limit: int = 100, offset: int = 0
    ) -> list[TimelineEvent]:
        """获取患者的健康时间线，聚合所有类型的健康事件。"""
        return self.store.get_timeline(patient_id=patient_id, limit=limit, offset=offset)

    # ── 预约 / 复诊 ────────────────────────────────────────────

    def create_appointment(self, data: AppointmentCreate) -> Appointment:
        return self.store.create_appointment(data)

    def get_appointment(self, appointment_id: int) -> Appointment:
        return self.store.get_appointment(appointment_id)

    def list_appointments(
        self,
        patient_id: int | None = None,
        status: str | None = None,
        upcoming_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Appointment], int]:
        return self.store.list_appointments(
            patient_id=patient_id,
            status=status,
            upcoming_only=upcoming_only,
            limit=limit,
            offset=offset,
        )

    def update_appointment(self, appointment_id: int, data: AppointmentUpdate) -> Appointment:
        return self.store.update_appointment(appointment_id, data)

    def delete_appointment(self, appointment_id: int) -> None:
        self.store.delete_appointment(appointment_id)

    # ── 服药记录 / 用药依从性 ──────────────────────────────────

    def create_medication_log(self, data: MedicationLogCreate) -> MedicationLog:
        return self.store.create_medication_log(data)

    def get_medication_log(self, log_id: int) -> MedicationLog:
        return self.store.get_medication_log(log_id)

    def list_medication_logs(
        self,
        patient_id: int | None = None,
        medication_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[MedicationLog], int]:
        return self.store.list_medication_logs(
            patient_id=patient_id,
            medication_id=medication_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )

    def delete_medication_log(self, log_id: int) -> None:
        self.store.delete_medication_log(log_id)

    def get_medication_adherence(self, patient_id: int, days: int = 30) -> dict:
        return self.store.get_medication_adherence(patient_id, days)

    # ── 药物相互作用检查 ───────────────────────────────────────

    def check_drug_interactions(
        self, drug_name: str, existing_drugs: list[str]
    ) -> list[DrugInteraction]:
        """检查新药与现有药物的相互作用。"""
        return self.store.check_drug_interactions(drug_name, existing_drugs)
