"""健康管理系统业务逻辑层。

封装存储层，提供患者档案管理、就诊记录、健康问题追踪、
用药管理、化验结果、检查记录、过敏史、生命体征、疫苗接种等
高层业务接口。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .models import (
    Allergy,
    AllergyCreate,
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
    HealthInsight,
    HealthInsightCreate,
    HealthStats,
    Immunization,
    ImmunizationCreate,
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
    Vitals,
    VitalsCreate,
)
from .store import HealthStore

if TYPE_CHECKING:
    from ..storage.database import Database

logger = logging.getLogger(__name__)


class HealthService:
    """健康管理业务服务。

    封装存储层，提供高层业务接口。
    """

    def __init__(
        self,
        database: Database | None = None,
        db_path: str | None = None,
        llm_service: Any = None,
    ) -> None:
        self.store = HealthStore(database=database, db_path=db_path)
        self.store.initialize()
        self.llm_service = llm_service

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

    # ── 过敏史 ────────────────────────────────────────────────

    def create_allergy(self, data: AllergyCreate) -> Allergy:
        return self.store.create_allergy(data)

    def get_allergy(self, allergy_id: int) -> Allergy:
        allergies = self.store.list_allergies()
        for a in allergies:
            if a.id == allergy_id:
                return a
        raise ValueError(f"过敏记录不存在: {allergy_id}")

    def list_allergies(self, patient_id: int | None = None) -> list[Allergy]:
        return self.store.list_allergies(patient_id=patient_id)

    def delete_allergy(self, allergy_id: int) -> None:
        self.store.delete_allergy(allergy_id)

    # ── 生命体征 ──────────────────────────────────────────────

    def create_vitals(self, data: VitalsCreate) -> Vitals:
        return self.store.create_vitals(data)

    def get_vitals(self, vitals_id: int) -> Vitals:
        return self.store.get_vitals(vitals_id)

    def list_vitals(
        self, patient_id: int, limit: int = 100, offset: int = 0
    ) -> tuple[list[Vitals], int]:
        return self.store.list_vitals(patient_id=patient_id, limit=limit, offset=offset)

    def delete_vitals(self, vitals_id: int) -> None:
        self.store.delete_vitals(vitals_id)

    # ── 疫苗接种 ──────────────────────────────────────────────

    def create_immunization(self, data: ImmunizationCreate) -> Immunization:
        return self.store.create_immunization(data)

    def get_immunization(self, immunization_id: int) -> Immunization:
        return self.store.get_immunization(immunization_id)

    def list_immunizations(self, patient_id: int | None = None) -> list[Immunization]:
        return self.store.list_immunizations(patient_id=patient_id)

    def delete_immunization(self, immunization_id: int) -> None:
        self.store.delete_immunization(immunization_id)

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
        allergies = self.store.list_allergies(patient_id=patient_id)
        _, vitals_count = self.store.list_vitals(patient_id=patient_id, limit=1)
        immunizations = self.store.list_immunizations(patient_id=patient_id)
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
                "allergies": len(allergies),
                "vitals": vitals_count,
                "immunizations": len(immunizations),
                "documents": doc_count,
                "pending_follow_ups": len(pending_procedures),
            },
            "active_conditions": [c.model_dump(mode="json") for c in active_conditions],
            "active_medications": [m.model_dump(mode="json") for m in active_medications],
            "allergies": [a.model_dump(mode="json") for a in allergies],
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

    # ── AI 健康洞察 ────────────────────────────────────────────

    def create_insight(self, data: HealthInsightCreate) -> HealthInsight:
        return self.store.create_insight(data)

    def get_insight(self, insight_id: int) -> HealthInsight:
        return self.store.get_insight(insight_id)

    def list_insights(
        self,
        patient_id: int | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        limit: int = 50,
    ) -> list[HealthInsight]:
        return self.store.list_insights(
            patient_id=patient_id,
            target_type=target_type,
            target_id=target_id,
            limit=limit,
        )

    def delete_insight(self, insight_id: int) -> None:
        self.store.delete_insight(insight_id)

    # ── 健康时间线 ──────────────────────────────────────────────

    def get_timeline(
        self, patient_id: int, limit: int = 100, offset: int = 0
    ) -> list[TimelineEvent]:
        """获取患者的健康时间线，聚合所有类型的健康事件。"""
        return self.store.get_timeline(patient_id=patient_id, limit=limit, offset=offset)

    # ── AI 报告解读 ────────────────────────────────────────────

    async def interpret_lab_result(self, lab_result_id: int) -> HealthInsight | None:
        """使用 LLM 解读化验报告。"""
        if self.llm_service is None:
            return None
        lab = self.store.get_lab_result(lab_result_id)
        components = self.store.list_lab_components(lab_result_id)
        components_text = "\n".join(
            f"- {c.test_name}: {c.value} {c.unit or ''} "
            f"(参考范围 {c.reference_range or '无'}) "
            f"[{'异常' if c.status != 'normal' else '正常'}]"
            for c in components
        )
        prompt = f"""你是一位专业的健康报告解读助手。请用通俗、客观的语言解读以下化验报告。
注意：你只能做信息整理和科普解释，不能给出确诊或治疗方案，必须建议咨询专业医生。

报告名称：{lab.test_name}
检验机构：{lab.facility or "未知"}
检验日期：{lab.completed_date or "未知"}

检验项目：
{components_text}

整体结论：{lab.overall_interpretation or "无"}

请按以下结构输出：
1. 【总体概况】一句话总结
2. 【异常项说明】逐项解释异常指标的可能含义（用通俗语言）
3. 【正常项确认】确认主要指标正常
4. 【建议方向】给出就医或生活方式的建议方向（非治疗方案）
5. 【免责声明】明确说明本解读仅供参考，不能替代医生诊断"""

        try:
            response = await self.llm_service.complete(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2000,
            )
            content = response.content if hasattr(response, "content") else str(response)
            insight = self.store.create_insight(
                HealthInsightCreate(
                    patient_id=lab.patient_id,
                    target_type="lab_result",
                    target_id=lab_result_id,
                    insight_type="interpretation",
                    content=content,
                    model=getattr(response, "model", ""),
                )
            )
            return insight
        except Exception as exc:
            logging.getLogger(__name__).warning("AI解读化验报告失败: %s", exc)
            return None

    async def interpret_procedure(self, procedure_id: int) -> HealthInsight | None:
        """使用 LLM 解读检查报告。"""
        if self.llm_service is None:
            return None
        proc = self.store.get_procedure(procedure_id)
        prompt = f"""你是一位专业的健康报告解读助手。请用通俗、客观的语言解读以下检查报告。
注意：你只能做信息整理和科普解释，不能给出确诊或治疗方案，必须建议咨询专业医生。

检查名称：{proc.procedure_name}
检查类型：{proc.procedure_type}
检查部位：{proc.body_part or "未知"}
检查机构：{proc.facility or "未知"}
检查日期：{proc.procedure_date}

检查所见：
{proc.findings or "无"}

检查结论：
{proc.conclusion or "无"}

异常摘要：{proc.abnormal_summary or "无"}
随访建议：{proc.follow_up_recommendation or "无"}

请按以下结构输出：
1. 【总体概况】一句话总结
2. 【关键发现】用通俗语言解释主要发现
3. 【需要关注】说明需要关注的问题及可能含义
4. 【建议方向】给出就医或随访的建议方向（非治疗方案）
5. 【免责声明】明确说明本解读仅供参考，不能替代医生诊断"""

        try:
            response = await self.llm_service.complete(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2000,
            )
            content = response.content if hasattr(response, "content") else str(response)
            insight = self.store.create_insight(
                HealthInsightCreate(
                    patient_id=proc.patient_id,
                    target_type="procedure",
                    target_id=procedure_id,
                    insight_type="interpretation",
                    content=content,
                    model=getattr(response, "model", ""),
                )
            )
            return insight
        except Exception as exc:
            logging.getLogger(__name__).warning("AI解读检查报告失败: %s", exc)
            return None

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
