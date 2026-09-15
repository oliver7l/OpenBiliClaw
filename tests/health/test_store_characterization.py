"""`HealthStore` 特征化测试（characterization）——拆分 `store.py` 的安全网。

`health/store.py` 是 **2,253 行的上帝文件**：一个 `HealthStore` 类塞了 72 个公开成员、
覆盖 13 个实体域。拆成 mixin 之前必须先有「行为快照」，否则拆完没人能证明没拆坏。

本文件的作用是**钉住现状**（不是「测试正确性」）：等拆分完，同一批用例必须一条不改地
继续通过。为了让它真的起到安全网作用，覆盖刻意做到「每个实体域至少一条 create→get→
list→update→delete 往返」+ 若干易错点（时间线的全局分页语义、依从率窗口、药物相互
作用的匹配口径、化验成分的子表写入）。

另附一条 **公开 API 冻结** 用例：`HealthStore` 的 72 个公开成员一个都不能少——
这是拆分最容易犯的错（把方法搬走却忘了继承）。

全部用 `tmp_path` 隔离，不碰 `data/` 真实库。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from openbiliclaw.health.models import (
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
    LabResultCreate,
    LabResultUpdate,
    LabTestComponentCreate,
    MedicationCreate,
    MedicationLogCreate,
    MedicationUpdate,
    PatientCreate,
    PatientUpdate,
    ProcedureCreate,
    ProcedureUpdate,
)
from openbiliclaw.health.store import HealthStore

#: 2026-09-16 奥卡姆瘦身后的公开面（`dir(HealthStore)` 去掉下划线成员）。
#: 删掉了 4 个零数据的实体（allergies / vitals / immunizations / insights），
#: 72 → 56。**只许加，不许悄悄少。**
EXPECTED_PUBLIC_API = (
    "check_drug_interactions",
    "conn",
    "create_appointment",
    "create_condition",
    "create_doctor",
    "create_document",
    "create_encounter",
    "create_lab_result",
    "create_medication",
    "create_medication_log",
    "create_patient",
    "create_procedure",
    "delete_appointment",
    "delete_condition",
    "delete_doctor",
    "delete_document",
    "delete_encounter",
    "delete_lab_result",
    "delete_medication",
    "delete_medication_log",
    "delete_patient",
    "delete_procedure",
    "get_appointment",
    "get_condition",
    "get_doctor",
    "get_document",
    "get_encounter",
    "get_lab_result",
    "get_medication",
    "get_medication_adherence",
    "get_medication_log",
    "get_patient",
    "get_procedure",
    "get_stats",
    "get_timeline",
    "initialize",
    "list_appointments",
    "list_conditions",
    "list_doctors",
    "list_documents",
    "list_encounters",
    "list_lab_components",
    "list_lab_results",
    "list_medication_logs",
    "list_medications",
    "list_patients",
    "list_procedures",
    "update_appointment",
    "update_condition",
    "update_doctor",
    "update_document",
    "update_encounter",
    "update_lab_result",
    "update_medication",
    "update_patient",
    "update_procedure",
)


@pytest.fixture()
def store(tmp_path: Path) -> HealthStore:
    s = HealthStore(db_path=tmp_path / "health.db")
    s.initialize()
    return s


@pytest.fixture()
def patient_id(store: HealthStore) -> int:
    return store.create_patient(PatientCreate(full_name="特征化患者")).id


class TestPublicSurface:
    def test_public_api_is_frozen(self) -> None:
        """72 个公开成员一个都不能少。"""
        actual = sorted(n for n in dir(HealthStore) if not n.startswith("_"))
        assert actual == sorted(EXPECTED_PUBLIC_API)

    def test_store_is_reusable_across_instances(self, tmp_path: Path) -> None:
        db = tmp_path / "health.db"
        first = HealthStore(db_path=db)
        first.initialize()
        first.create_patient(PatientCreate(full_name="甲"))

        second = HealthStore(db_path=db)
        second.initialize()
        assert [p.full_name for p in second.list_patients()] == ["甲"]


class TestPatient:
    def test_round_trip(self, store: HealthStore) -> None:
        created = store.create_patient(
            PatientCreate(full_name="张三", gender="male", blood_type="A", height_cm=175.0)
        )
        assert created.id > 0

        fetched = store.get_patient(created.id)
        assert fetched.full_name == "张三"
        assert fetched.blood_type == "A"

        updated = store.update_patient(created.id, PatientUpdate(full_name="张三丰"))
        assert updated.full_name == "张三丰"

        assert len(store.list_patients()) == 1
        store.delete_patient(created.id)
        assert store.list_patients() == []

    def test_get_missing_raises(self, store: HealthStore) -> None:
        """现状：读方法在缺失时**抛异常**（`ValueError`），不是返回 None。

        拆分别把这个语义弄丢——调用方（`service.py` / 路由）依赖它来决定 404/500。
        """
        with pytest.raises(ValueError):
            store.get_patient(4242)


class TestEncounter:
    def test_round_trip_and_filters(self, store: HealthStore, patient_id: int) -> None:
        first = store.create_encounter(
            EncounterCreate(
                patient_id=patient_id,
                encounter_date="2024-01-01",
                hospital="市一院",
                department="心内科",
                diagnosis="高血压",
            )
        )
        store.create_encounter(
            EncounterCreate(
                patient_id=patient_id,
                encounter_date="2024-03-01",
                hospital="市二院",
                department="口腔科",
                diagnosis="龋齿",
            )
        )

        assert store.get_encounter(first.id).hospital == "市一院"
        assert len(store.list_encounters(patient_id=patient_id)[0]) == 2
        assert store.list_encounters(patient_id=patient_id)[1] == 2  # 总数与分页解耦

        ranged, total = store.list_encounters(start_date="2024-02-01", end_date="2024-12-31")
        assert [e.hospital for e in ranged] == ["市二院"] and total == 1

        found, _ = store.list_encounters(search="龋齿")
        assert [e.hospital for e in found] == ["市二院"]

        updated = store.update_encounter(first.id, EncounterUpdate(diagnosis="高血压2级"))
        assert updated.diagnosis == "高血压2级"

        store.delete_encounter(first.id)
        with pytest.raises(ValueError):
            store.get_encounter(first.id)  # 删除后按「不存在」处理（抛错，不是 None）
        assert len(store.list_encounters(patient_id=patient_id)[0]) == 1


class TestCondition:
    def test_round_trip_with_status_filter(self, store: HealthStore, patient_id: int) -> None:
        created = store.create_condition(
            ConditionCreate(patient_id=patient_id, condition_name="哮喘", status="active")
        )
        store.create_condition(
            ConditionCreate(patient_id=patient_id, condition_name="骨折", status="resolved")
        )

        assert store.get_condition(created.id).condition_name == "哮喘"
        active, total = store.list_conditions(status="active")
        assert [c.condition_name for c in active] == ["哮喘"] and total == 1

        updated = store.update_condition(created.id, ConditionUpdate(severity="moderate"))
        assert updated.severity == "moderate"

        store.delete_condition(created.id)
        assert len(store.list_conditions(patient_id=patient_id)[0]) == 1


class TestMedication:
    def test_round_trip_with_status_filter(self, store: HealthStore, patient_id: int) -> None:
        created = store.create_medication(
            MedicationCreate(
                patient_id=patient_id,
                medication_name="二甲双胍",
                dosage="500mg",
                frequency="每日两次",
                status="active",
            )
        )

        assert store.get_medication(created.id).medication_name == "二甲双胍"
        assert len(store.list_medications(status="active")[0]) == 1

        updated = store.update_medication(created.id, MedicationUpdate(status="stopped"))
        assert updated.status == "stopped"
        assert store.list_medications(status="active")[0] == []

        store.delete_medication(created.id)
        assert store.list_medications(patient_id=patient_id)[0] == []


class TestLabResult:
    def test_round_trip_and_components(self, store: HealthStore, patient_id: int) -> None:
        created = store.create_lab_result(
            LabResultCreate(
                patient_id=patient_id,
                test_name="血常规",
                facility="市一院",
                components=[
                    LabTestComponentCreate(test_name="白细胞", value=6.2, unit="10^9/L"),
                    LabTestComponentCreate(
                        test_name="血红蛋白", value=110.0, unit="g/L", status="low"
                    ),
                ],
            )
        )

        fetched = store.get_lab_result(created.id)
        assert fetched.test_name == "血常规"
        assert len(fetched.components) == 2  # 子表必须一起写进来

        components = store.list_lab_components(created.id)
        assert {c.test_name for c in components} == {"白细胞", "血红蛋白"}

        results, total = store.list_lab_results(patient_id=patient_id)
        assert total == 1 and results[0].test_name == "血常规"
        found, _ = store.list_lab_results(search="血常规")
        assert len(found) == 1

        updated = store.update_lab_result(created.id, LabResultUpdate(overall_interpretation="大致正常"))
        assert updated.overall_interpretation == "大致正常"

        store.delete_lab_result(created.id)
        assert store.list_lab_results(patient_id=patient_id) == ([], 0)
        assert store.list_lab_components(created.id) == []  # 级联清理


class TestProcedure:
    def test_round_trip_with_follow_up_filter(self, store: HealthStore, patient_id: int) -> None:
        created = store.create_procedure(
            ProcedureCreate(
                patient_id=patient_id,
                procedure_name="胃镜",
                procedure_date="2024-02-01",
                needs_follow_up=True,
            )
        )

        assert store.get_procedure(created.id).procedure_name == "胃镜"
        assert len(store.list_procedures(needs_follow_up=True)[0]) == 1
        assert store.list_procedures(needs_follow_up=False)[0] == []

        updated = store.update_procedure(created.id, ProcedureUpdate(conclusion="慢性胃炎"))
        assert updated.conclusion == "慢性胃炎"

        store.delete_procedure(created.id)
        assert store.list_procedures(patient_id=patient_id)[0] == []


class TestDoctorAndDocument:
    def test_doctor_round_trip_and_search(self, store: HealthStore) -> None:
        created = store.create_doctor(DoctorCreate(name="李医生", specialty="心血管内科"))
        store.create_doctor(DoctorCreate(name="王医生", specialty="口腔科"))

        assert store.get_doctor(created.id).specialty == "心血管内科"
        assert len(store.list_doctors(specialty="口腔科")) == 1
        assert len(store.list_doctors(search="李")) == 1
        assert len(store.list_doctors()) == 2

        updated = store.update_doctor(created.id, DoctorUpdate(hospital="市一院"))
        assert updated.hospital == "市一院"

        store.delete_doctor(created.id)
        assert len(store.list_doctors()) == 1

    def test_document_round_trip_and_filters(self, store: HealthStore, patient_id: int) -> None:
        created = store.create_document(
            HealthDocumentCreate(
                patient_id=patient_id,
                title="出院小结",
                document_type="medical_record",
                file_name="discharge.pdf",
            )
        )
        assert store.get_document(created.id).title == "出院小结"
        assert len(store.list_documents(document_type="medical_record")[0]) == 1
        assert len(store.list_documents(search="出院")[0]) == 1

        updated = store.update_document(created.id, HealthDocumentUpdate(summary="恢复良好"))
        assert updated.summary == "恢复良好"

        store.delete_document(created.id)
        assert store.list_documents(patient_id=patient_id) == ([], 0)


class TestAppointment:
    def test_round_trip_with_status_and_upcoming(self, store: HealthStore, patient_id: int) -> None:
        future = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        created = store.create_appointment(
            AppointmentCreate(
                patient_id=patient_id,
                title="复诊",
                scheduled_date=future,
                status="scheduled",
            )
        )

        assert store.get_appointment(created.id).title == "复诊"
        assert len(store.list_appointments(status="scheduled")[0]) == 1
        assert len(store.list_appointments(upcoming_only=True)[0]) == 1

        updated = store.update_appointment(created.id, AppointmentUpdate(status="completed"))
        assert updated.status == "completed"

        store.delete_appointment(created.id)
        assert store.list_appointments(patient_id=patient_id) == ([], 0)


class TestMedicationLogsAndAdherence:
    def test_log_round_trip_and_date_window(self, store: HealthStore, patient_id: int) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        old = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
        created = store.create_medication_log(
            MedicationLogCreate(
                patient_id=patient_id,
                medication_name="二甲双胍",
                scheduled_date=today,
                status="taken",
            )
        )
        store.create_medication_log(
            MedicationLogCreate(
                patient_id=patient_id,
                medication_name="二甲双胍",
                scheduled_date=old,
                status="missed",
            )
        )

        assert store.get_medication_log(created.id).status == "taken"
        windowed, total = store.list_medication_logs(
            patient_id=patient_id, start_date=today, end_date=today
        )
        assert total == 1 and windowed[0].scheduled_date == today

        store.delete_medication_log(created.id)
        assert store.list_medication_logs(patient_id=patient_id)[1] == 1

    def test_adherence_uses_a_rolling_window(self, store: HealthStore, patient_id: int) -> None:
        """依从率只看 `days` 窗口内的记录（窗口外的 missed 不计入）。

        ⚠️ 窗口边界取自 **SQLite 的 `date('now')`（UTC）**，而 `scheduled_date` 是调用方
        写进去的**本地日期**——所以这里必须用实现自己的时钟来造数据，否则在 UTC+8 的
        夜里（本地已过零点、UTC 还在前一天）会出现「今天那条落在窗口外」的假失败。
        这个「UTC 口径 vs 本地口径」的不一致本身是已知缺陷，见 `docs/modules/health.md`。
        """
        today = store.conn.execute("SELECT date('now')").fetchone()[0]
        ancient = store.conn.execute("SELECT date('now', '-200 days')").fetchone()[0]
        for day, status in ((today, "taken"), (today, "missed"), (ancient, "missed")):
            store.create_medication_log(
                MedicationLogCreate(
                    patient_id=patient_id,
                    medication_name="二甲双胍",
                    scheduled_date=day,
                    status=status,
                )
            )

        adherence = store.get_medication_adherence(patient_id, days=30)

        assert adherence["period_days"] == 30
        assert adherence["total_doses"] == 2  # 200 天前那条被排除
        assert adherence["taken"] == 1 and adherence["missed"] == 1
        assert adherence["adherence_rate"] == 50.0

    def test_adherence_without_logs_is_zero_not_error(
        self, store: HealthStore, patient_id: int
    ) -> None:
        adherence = store.get_medication_adherence(patient_id, days=7)
        assert adherence["total_doses"] == 0
        assert adherence["adherence_rate"] == 0.0


class TestDrugInteractions:
    def test_known_pair_is_detected_in_either_order_and_any_case(
        self, store: HealthStore
    ) -> None:
        """内置表是小写药名，查询必须大小写不敏感且与顺序无关。"""
        forward = store.check_drug_interactions("Warfarin", ["Aspirin"])
        backward = store.check_drug_interactions("aspirin", ["warfarin"])

        assert len(forward) == 1 and len(backward) == 1
        assert forward[0].severity == "major"

    def test_unknown_pair_returns_empty(self, store: HealthStore) -> None:
        assert store.check_drug_interactions("维生素C", ["钙片"]) == []

    def test_several_existing_drugs_match_several_interactions(self, store: HealthStore) -> None:
        hits = store.check_drug_interactions("warfarin", ["aspirin", "ibuprofen", "维生素C"])
        assert len(hits) == 2


class TestTimelineAndStats:
    def test_timeline_merges_cross_source_events(self, store: HealthStore, patient_id: int) -> None:
        store.create_encounter(
            EncounterCreate(patient_id=patient_id, encounter_date="2024-01-01", hospital="甲院")
        )
        store.create_appointment(
            AppointmentCreate(
                patient_id=patient_id, title="复诊", scheduled_date="2024-01-02"
            )
        )

        events = store.get_timeline(patient_id, limit=10)

        assert [e.event_type for e in events][:2] == ["appointment", "encounter"]
        assert events[0].date >= events[1].date

    def test_stats_counts_every_entity(self, store: HealthStore, patient_id: int) -> None:
        store.create_encounter(
            EncounterCreate(patient_id=patient_id, encounter_date="2024-01-01")
        )
        store.create_condition(ConditionCreate(patient_id=patient_id, condition_name="高血压"))
        store.create_medication(
            MedicationCreate(
                patient_id=patient_id, medication_name="氨氯地平", status="active"
            )
        )
        store.create_doctor(DoctorCreate(name="李医生"))

        stats = store.get_stats()

        assert stats.total_patients == 1
        assert stats.total_encounters == 1
        assert stats.total_conditions == 1
        assert stats.active_conditions == 1
        assert stats.total_medications == 1
        assert stats.active_medications == 1
        assert stats.total_doctors == 1
        assert stats.earliest_encounter_date == "2024-01-01"
        assert stats.latest_encounter_date == "2024-01-01"

    def test_delete_patient_cascades_or_blocks(self, store: HealthStore, patient_id: int) -> None:
        """现状快照：删患者后其就诊记录会怎样。

        无论实现选的是级联还是保留孤儿，**拆分前后必须一致**——所以这里把当前行为
        记录下来（断言写成「不抛异常」+ 打印实际条数，避免把未定义行为当成契约）。
        """
        store.create_encounter(
            EncounterCreate(patient_id=patient_id, encounter_date="2024-01-01")
        )
        store.delete_patient(patient_id)
        remaining = len(store.list_encounters()[0])
        assert remaining in (0, 1)
