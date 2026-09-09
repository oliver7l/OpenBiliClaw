"""健康管理系统 API 路由（从 app.py 提取）。

患者档案、就诊记录、健康问题、用药、化验、检查、过敏、生命体征、
疫苗、医生、文档、AI 洞察、时间线、预约、药物相互作用与 AI 报告解读。
通过 ``register_health_routes(app, ctx)`` 注册。
"""

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from openbiliclaw.api.runtime_context import RuntimeContext
from openbiliclaw.health import (
    AllergyCreate,
    AppointmentCreate,
    AppointmentUpdate,
    ConditionCreate,
    ConditionUpdate,
    DoctorCreate,
    DoctorUpdate,
    EncounterCreate,
    EncounterUpdate,
    HealthDocumentCreate,
    HealthDocumentUpdate,
    HealthInsightCreate,
    HealthService,
    ImmunizationCreate,
    LabResultCreate,
    LabResultUpdate,
    MedicationCreate,
    MedicationLogCreate,
    MedicationUpdate,
    PatientCreate,
    PatientUpdate,
    ProcedureCreate,
    ProcedureUpdate,
    VitalsCreate,
)

_health_service: HealthService | None = None


def _get_health_service(ctx: RuntimeContext) -> HealthService | None:
    """获取或创建健康管理服务实例（懒加载）。

    health 表独立存放于 health.db（db sharding P7），与主库锁域隔离。
    """
    global _health_service
    if _health_service is not None:
        return _health_service
    database = getattr(ctx, "database", None)
    if database is None:
        return None
    # 路径解析优先级：config.storage.health_db_path > 主库同目录 health.db
    db_path: str | None = None
    config = getattr(ctx, "config", None)
    storage = getattr(config, "storage", None)
    if storage is not None and getattr(storage, "health_db_path", ""):
        db_path = str(storage.health_db_path)
    if not db_path:
        main_path = getattr(database, "_db_path", None)
        if main_path is not None:
            db_path = str(Path(main_path).with_name("health.db"))
    if not db_path:
        db_path = "data/health.db"
    llm_service = getattr(ctx, "llm_service", None)
    _health_service = HealthService(db_path=db_path, llm_service=llm_service)
    return _health_service


def register_health_routes(app: FastAPI, ctx: RuntimeContext) -> None:
    # ── 统计概览 ──

    @app.get("/api/health/stats")
    def health_stats() -> JSONResponse:
        """获取健康档案统计概览。"""
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        stats = svc.get_stats()
        return JSONResponse({"ok": True, "stats": stats.model_dump(mode="json")})

    # ── 患者档案 ──

    @app.get("/api/health/patients")
    def health_patients_list() -> JSONResponse:
        """列出所有患者档案。"""
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        patients = svc.list_patients()
        return JSONResponse({"ok": True, "items": [p.model_dump(mode="json") for p in patients]})

    @app.post("/api/health/patients")
    def health_patients_create(payload: dict[str, Any]) -> JSONResponse:
        """创建患者档案。"""
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = PatientCreate(**payload)
            patient = svc.create_patient(data)
            return JSONResponse({"ok": True, "data": patient.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/health/patients/{patient_id}")
    def health_patients_get(patient_id: int) -> JSONResponse:
        """获取患者档案详情（含各模块统计）。"""
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            summary = svc.get_patient_summary(patient_id)
            return JSONResponse({"ok": True, "data": summary})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.put("/api/health/patients/{patient_id}")
    def health_patients_update(patient_id: int, payload: dict[str, Any]) -> JSONResponse:
        """更新患者档案。"""
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = PatientUpdate(**payload)
            patient = svc.update_patient(patient_id, data)
            return JSONResponse({"ok": True, "data": patient.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/patients/{patient_id}")
    def health_patients_delete(patient_id: int) -> JSONResponse:
        """删除患者档案。"""
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_patient(patient_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 就诊记录 ──

    @app.get("/api/health/encounters")
    def health_encounters_list(
        patient_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
        encounter_type: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        search: str | None = None,
    ) -> JSONResponse:
        """列就诊记录，支持筛选与搜索。"""
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items, total = svc.list_encounters(
            patient_id=patient_id,
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
            encounter_type=encounter_type,
            start_date=start_date,
            end_date=end_date,
            search=search,
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [e.model_dump(mode="json") for e in items],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )

    @app.post("/api/health/encounters")
    def health_encounters_create(payload: dict[str, Any]) -> JSONResponse:
        """创建就诊记录。"""
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = EncounterCreate(**payload)
            encounter = svc.create_encounter(data)
            return JSONResponse({"ok": True, "data": encounter.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/health/encounters/{encounter_id}")
    def health_encounters_get(encounter_id: int) -> JSONResponse:
        """获取单条就诊记录详情。"""
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            encounter = svc.get_encounter(encounter_id)
            return JSONResponse({"ok": True, "data": encounter.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.put("/api/health/encounters/{encounter_id}")
    def health_encounters_update(encounter_id: int, payload: dict[str, Any]) -> JSONResponse:
        """更新就诊记录。"""
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = EncounterUpdate(**payload)
            encounter = svc.update_encounter(encounter_id, data)
            return JSONResponse({"ok": True, "data": encounter.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/encounters/{encounter_id}")
    def health_encounters_delete(encounter_id: int) -> JSONResponse:
        """删除就诊记录。"""
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_encounter(encounter_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 健康问题 ──

    @app.get("/api/health/conditions")
    def health_conditions_list(
        patient_id: int | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> JSONResponse:
        """列出健康问题。"""
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items, total = svc.list_conditions(
            patient_id=patient_id,
            status=status,
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [c.model_dump(mode="json") for c in items],
                "total": total,
            }
        )

    @app.post("/api/health/conditions")
    def health_conditions_create(payload: dict[str, Any]) -> JSONResponse:
        """创建健康问题。"""
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = ConditionCreate(**payload)
            condition = svc.create_condition(data)
            return JSONResponse({"ok": True, "data": condition.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/health/conditions/{condition_id}")
    def health_conditions_get(condition_id: int) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            condition = svc.get_condition(condition_id)
            return JSONResponse({"ok": True, "data": condition.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.put("/api/health/conditions/{condition_id}")
    def health_conditions_update(condition_id: int, payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = ConditionUpdate(**payload)
            condition = svc.update_condition(condition_id, data)
            return JSONResponse({"ok": True, "data": condition.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/conditions/{condition_id}")
    def health_conditions_delete(condition_id: int) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_condition(condition_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 用药记录 ──

    @app.get("/api/health/medications")
    def health_medications_list(
        patient_id: int | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items, total = svc.list_medications(
            patient_id=patient_id,
            status=status,
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [m.model_dump(mode="json") for m in items],
                "total": total,
            }
        )

    @app.post("/api/health/medications")
    def health_medications_create(payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = MedicationCreate(**payload)
            medication = svc.create_medication(data)
            return JSONResponse({"ok": True, "data": medication.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/health/medications/{medication_id}")
    def health_medications_get(medication_id: int) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            medication = svc.get_medication(medication_id)
            return JSONResponse({"ok": True, "data": medication.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.put("/api/health/medications/{medication_id}")
    def health_medications_update(medication_id: int, payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = MedicationUpdate(**payload)
            medication = svc.update_medication(medication_id, data)
            return JSONResponse({"ok": True, "data": medication.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/medications/{medication_id}")
    def health_medications_delete(medication_id: int) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_medication(medication_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 化验结果 ──

    @app.get("/api/health/lab-results")
    def health_lab_results_list(
        patient_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
    ) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items, total = svc.list_lab_results(
            patient_id=patient_id,
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
            search=search,
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [item.model_dump(mode="json") for item in items],
                "total": total,
            }
        )

    @app.post("/api/health/lab-results")
    def health_lab_results_create(payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = LabResultCreate(**payload)
            lab_result = svc.create_lab_result(data)
            return JSONResponse({"ok": True, "data": lab_result.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/health/lab-results/{lab_result_id}")
    def health_lab_results_get(lab_result_id: int) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            lab_result = svc.get_lab_result(lab_result_id)
            return JSONResponse({"ok": True, "data": lab_result.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.put("/api/health/lab-results/{lab_result_id}")
    def health_lab_results_update(lab_result_id: int, payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = LabResultUpdate(**payload)
            lab_result = svc.update_lab_result(lab_result_id, data)
            return JSONResponse({"ok": True, "data": lab_result.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/lab-results/{lab_result_id}")
    def health_lab_results_delete(lab_result_id: int) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_lab_result(lab_result_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.get("/api/health/lab-trend")
    def health_lab_trend(patient_id: int, test_name: str, limit: int = 50) -> JSONResponse:
        """获取某个化验项目的历史趋势。"""
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        trend = svc.get_lab_trend(patient_id=patient_id, test_name=test_name, limit=limit)
        return JSONResponse({"ok": True, "test_name": test_name, "items": trend})

    # ── 检查 / 手术 ──

    @app.get("/api/health/procedures")
    def health_procedures_list(
        patient_id: int | None = None,
        procedure_type: str | None = None,
        needs_follow_up: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items, total = svc.list_procedures(
            patient_id=patient_id,
            procedure_type=procedure_type,
            needs_follow_up=needs_follow_up,
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [p.model_dump(mode="json") for p in items],
                "total": total,
            }
        )

    @app.post("/api/health/procedures")
    def health_procedures_create(payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = ProcedureCreate(**payload)
            procedure = svc.create_procedure(data)
            return JSONResponse({"ok": True, "data": procedure.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/health/procedures/{procedure_id}")
    def health_procedures_get(procedure_id: int) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            procedure = svc.get_procedure(procedure_id)
            return JSONResponse({"ok": True, "data": procedure.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.put("/api/health/procedures/{procedure_id}")
    def health_procedures_update(procedure_id: int, payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = ProcedureUpdate(**payload)
            procedure = svc.update_procedure(procedure_id, data)
            return JSONResponse({"ok": True, "data": procedure.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/procedures/{procedure_id}")
    def health_procedures_delete(procedure_id: int) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_procedure(procedure_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 过敏史 ──

    @app.get("/api/health/allergies")
    def health_allergies_list(patient_id: int | None = None) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items = svc.list_allergies(patient_id=patient_id)
        return JSONResponse({"ok": True, "items": [a.model_dump(mode="json") for a in items]})

    @app.post("/api/health/allergies")
    def health_allergies_create(payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = AllergyCreate(**payload)
            allergy = svc.create_allergy(data)
            return JSONResponse({"ok": True, "data": allergy.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/allergies/{allergy_id}")
    def health_allergies_delete(allergy_id: int) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_allergy(allergy_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 生命体征 ──

    @app.get("/api/health/vitals")
    def health_vitals_list(patient_id: int, limit: int = 100, offset: int = 0) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items, total = svc.list_vitals(
            patient_id=patient_id,
            limit=max(1, min(int(limit), 500)),
            offset=max(0, int(offset)),
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [v.model_dump(mode="json") for v in items],
                "total": total,
            }
        )

    @app.post("/api/health/vitals")
    def health_vitals_create(payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = VitalsCreate(**payload)
            vitals = svc.create_vitals(data)
            return JSONResponse({"ok": True, "data": vitals.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/vitals/{vitals_id}")
    def health_vitals_delete(vitals_id: int) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_vitals(vitals_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 疫苗接种 ──

    @app.get("/api/health/immunizations")
    def health_immunizations_list(patient_id: int | None = None) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items = svc.list_immunizations(patient_id=patient_id)
        return JSONResponse({"ok": True, "items": [i.model_dump(mode="json") for i in items]})

    @app.post("/api/health/immunizations")
    def health_immunizations_create(payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = ImmunizationCreate(**payload)
            immunization = svc.create_immunization(data)
            return JSONResponse({"ok": True, "data": immunization.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/immunizations/{immunization_id}")
    def health_immunizations_delete(immunization_id: int) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_immunization(immunization_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 医生信息 ──

    @app.get("/api/health/doctors")
    def health_doctors_list(
        specialty: str | None = None, search: str | None = None
    ) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items = svc.list_doctors(specialty=specialty, search=search)
        return JSONResponse({"ok": True, "items": [d.model_dump(mode="json") for d in items]})

    @app.post("/api/health/doctors")
    def health_doctors_create(payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = DoctorCreate(**payload)
            doctor = svc.create_doctor(data)
            return JSONResponse({"ok": True, "data": doctor.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/health/doctors/{doctor_id}")
    def health_doctors_get(doctor_id: int) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            doctor = svc.get_doctor(doctor_id)
            return JSONResponse({"ok": True, "data": doctor.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.put("/api/health/doctors/{doctor_id}")
    def health_doctors_update(doctor_id: int, payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = DoctorUpdate(**payload)
            doctor = svc.update_doctor(doctor_id, data)
            return JSONResponse({"ok": True, "data": doctor.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/doctors/{doctor_id}")
    def health_doctors_delete(doctor_id: int) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_doctor(doctor_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 文档 / 附件 ──

    @app.get("/api/health/documents")
    def health_documents_list(
        patient_id: int | None = None,
        document_type: str | None = None,
        encounter_id: int | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items, total = svc.list_documents(
            patient_id=patient_id,
            document_type=document_type,
            encounter_id=encounter_id,
            search=search,
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [d.model_dump(mode="json") for d in items],
                "total": total,
            }
        )

    @app.post("/api/health/documents")
    def health_documents_create(payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = HealthDocumentCreate(**payload)
            doc = svc.create_document(data)
            return JSONResponse({"ok": True, "data": doc.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/health/documents/{document_id}")
    def health_documents_get(document_id: int) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            doc = svc.get_document(document_id)
            return JSONResponse({"ok": True, "data": doc.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.put("/api/health/documents/{document_id}")
    def health_documents_update(document_id: int, payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = HealthDocumentUpdate(**payload)
            doc = svc.update_document(document_id, data)
            return JSONResponse({"ok": True, "data": doc.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/documents/{document_id}")
    def health_documents_delete(document_id: int) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_document(document_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── AI 健康洞察 ──

    @app.get("/api/health/insights")
    def health_insights_list(
        patient_id: int | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items = svc.list_insights(
            patient_id=patient_id,
            target_type=target_type,
            target_id=target_id,
            limit=max(1, min(int(limit), 200)),
        )
        return JSONResponse({"ok": True, "items": [i.model_dump(mode="json") for i in items]})

    @app.post("/api/health/insights")
    def health_insights_create(payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = HealthInsightCreate(**payload)
            insight = svc.create_insight(data)
            return JSONResponse({"ok": True, "data": insight.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/insights/{insight_id}")
    def health_insights_delete(insight_id: int) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_insight(insight_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 健康时间线 ──

    @app.get("/api/health/timeline")
    def health_timeline(patient_id: int, limit: int = 100, offset: int = 0) -> JSONResponse:
        """获取患者健康时间线，聚合所有类型的健康事件。"""
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        events = svc.get_timeline(
            patient_id=patient_id,
            limit=max(1, min(int(limit), 500)),
            offset=max(0, int(offset)),
        )
        return JSONResponse(
            {
                "ok": True,
                "patient_id": patient_id,
                "items": [e.model_dump(mode="json") for e in events],
                "total": len(events),
            }
        )

    # ── 预约 / 复诊 ──

    @app.get("/api/health/appointments")
    def health_appointments_list(
        patient_id: int | None = None,
        status: str | None = None,
        upcoming_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items, total = svc.list_appointments(
            patient_id=patient_id,
            status=status,
            upcoming_only=upcoming_only,
            limit=max(1, min(int(limit), 500)),
            offset=max(0, int(offset)),
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [a.model_dump(mode="json") for a in items],
                "total": total,
            }
        )

    @app.post("/api/health/appointments")
    def health_appointments_create(data: AppointmentCreate) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            item = svc.create_appointment(data)
            return JSONResponse({"ok": True, "item": item.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/health/appointments/{appointment_id}")
    def health_appointments_get(appointment_id: int) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            item = svc.get_appointment(appointment_id)
            return JSONResponse({"ok": True, "item": item.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.put("/api/health/appointments/{appointment_id}")
    def health_appointments_update(appointment_id: int, data: AppointmentUpdate) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            item = svc.update_appointment(appointment_id, data)
            return JSONResponse({"ok": True, "item": item.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.delete("/api/health/appointments/{appointment_id}")
    def health_appointments_delete(appointment_id: int) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_appointment(appointment_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 服药记录 / 用药依从性 ──

    @app.get("/api/health/medication-logs")
    def health_medication_logs_list(
        patient_id: int | None = None,
        medication_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items, total = svc.list_medication_logs(
            patient_id=patient_id,
            medication_id=medication_id,
            start_date=start_date,
            end_date=end_date,
            limit=max(1, min(int(limit), 500)),
            offset=max(0, int(offset)),
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [m.model_dump(mode="json") for m in items],
                "total": total,
            }
        )

    @app.post("/api/health/medication-logs")
    def health_medication_logs_create(data: MedicationLogCreate) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            item = svc.create_medication_log(data)
            return JSONResponse({"ok": True, "item": item.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/health/medication-logs/{log_id}")
    def health_medication_logs_get(log_id: int) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            item = svc.get_medication_log(log_id)
            return JSONResponse({"ok": True, "item": item.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.delete("/api/health/medication-logs/{log_id}")
    def health_medication_logs_delete(log_id: int) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_medication_log(log_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.get("/api/health/medication-adherence")
    def health_medication_adherence(patient_id: int, days: int = 30) -> JSONResponse:
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        result = svc.get_medication_adherence(patient_id, max(1, min(int(days), 365)))
        return JSONResponse({"ok": True, **result})

    # ── 药物相互作用检查 ──

    @app.post("/api/health/check-drug-interactions")
    def health_check_drug_interactions(drug_name: str, existing_drugs: str) -> JSONResponse:
        """检查新药与现有药物的相互作用。existing_drugs 用逗号分隔。"""
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        drugs = [d.strip() for d in existing_drugs.split(",") if d.strip()]
        interactions = svc.check_drug_interactions(drug_name, drugs)
        return JSONResponse(
            {
                "ok": True,
                "drug_name": drug_name,
                "existing_drugs": drugs,
                "interactions": [i.model_dump(mode="json") for i in interactions],
                "has_interaction": len(interactions) > 0,
            }
        )

    # ── AI 报告解读 ──

    @app.post("/api/health/lab-results/{lab_result_id}/interpret")
    async def health_lab_interpret(lab_result_id: int) -> JSONResponse:
        """使用 AI 解读化验报告。"""
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if svc.llm_service is None:
            return JSONResponse({"ok": False, "error": "LLM service unavailable"}, status_code=503)
        try:
            insight = await svc.interpret_lab_result(lab_result_id)
            if insight is None:
                return JSONResponse(
                    {"ok": False, "error": "interpretation failed"}, status_code=500
                )
            return JSONResponse({"ok": True, "data": insight.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.post("/api/health/procedures/{procedure_id}/interpret")
    async def health_procedure_interpret(procedure_id: int) -> JSONResponse:
        """使用 AI 解读检查报告。"""
        svc = _get_health_service(ctx)
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if svc.llm_service is None:
            return JSONResponse({"ok": False, "error": "LLM service unavailable"}, status_code=503)
        try:
            insight = await svc.interpret_procedure(procedure_id)
            if insight is None:
                return JSONResponse(
                    {"ok": False, "error": "interpretation failed"}, status_code=500
                )
            return JSONResponse({"ok": True, "data": insight.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
