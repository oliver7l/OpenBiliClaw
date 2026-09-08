"""健康管理系统数据模型。

参考 MediKeep (afairgiant/MediKeep) 的数据模型设计，
适配个人/家庭使用场景，使用 Pydantic 进行类型校验与序列化。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

# ── 枚举 ──────────────────────────────────────────────────────


class EncounterType(StrEnum):
    """就诊类型。"""

    OUTPATIENT = "outpatient"  # 门诊
    EMERGENCY = "emergency"  # 急诊
    INPATIENT = "inpatient"  # 住院
    PHYSICAL = "physical"  # 体检
    FOLLOW_UP = "follow_up"  # 复查
    TELEHEALTH = "telehealth"  # 线上问诊
    OTHER = "other"


class EncounterPriority(StrEnum):
    """就诊优先级。"""

    ROUTINE = "routine"
    URGENT = "urgent"
    EMERGENCY = "emergency"


class ConditionStatus(StrEnum):
    """健康问题状态。"""

    ACTIVE = "active"
    INACTIVE = "inactive"
    RESOLVED = "resolved"
    CHRONIC = "chronic"
    RECURRENCE = "recurrence"


class ConditionSeverity(StrEnum):
    """严重程度。"""

    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    CRITICAL = "critical"


class MedicationType(StrEnum):
    """药物类型。"""

    PRESCRIPTION = "prescription"  # 处方药
    OTC = "otc"  # 非处方药
    SUPPLEMENT = "supplement"  # 保健品
    HERBAL = "herbal"  # 中药/草药


class MedicationStatus(StrEnum):
    """用药状态。"""

    ACTIVE = "active"
    STOPPED = "stopped"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class LabResultStatus(StrEnum):
    """化验结果状态。"""

    ORDERED = "ordered"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class LabComponentStatus(StrEnum):
    """单个化验项目状态。"""

    NORMAL = "normal"
    HIGH = "high"
    LOW = "low"
    CRITICAL = "critical"
    PENDING = "pending"


class ProcedureType(StrEnum):
    """检查/手术类型。"""

    IMAGING = "imaging"  # 影像检查 (CT/MRI/超声/X光)
    LAB = "lab"  # 化验
    ENDOSCOPY = "endoscopy"  # 内镜
    SURGERY = "surgery"  # 手术
    PATHOLOGY = "pathology"  # 病理
    ECG = "ecg"  # 心电图
    OTHER = "other"


class ProcedureStatus(StrEnum):
    """检查/手术状态。"""

    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    POSTPONED = "postponed"


class AllergyStatus(StrEnum):
    """过敏状态。"""

    ACTIVE = "active"
    INACTIVE = "inactive"
    RESOLVED = "resolved"
    UNCONFIRMED = "unconfirmed"


class VitalGlucoseContext(StrEnum):
    """血糖测量上下文。"""

    FASTING = "fasting"
    BEFORE_MEAL = "before_meal"
    AFTER_MEAL = "after_meal"
    RANDOM = "random"


# ── 患者 ──────────────────────────────────────────────────────


class Patient(BaseModel):
    """患者档案。"""

    id: int = Field(description="患者唯一 ID")
    full_name: str = Field(description="姓名")
    gender: str = Field(default="", description="性别")
    birth_date: str | None = Field(default=None, description="出生日期 YYYY-MM-DD")
    blood_type: str = Field(default="", description="血型，如 A+、O-")
    height_cm: float | None = Field(default=None, description="身高 cm")
    weight_kg: float | None = Field(default=None, description="体重 kg")
    phone: str = Field(default="", description="联系电话")
    relationship: str = Field(
        default="self", description="与本人关系：self/spouse/child/parent/other"
    )
    emergency_contact_name: str = Field(default="", description="紧急联系人姓名")
    emergency_contact_phone: str = Field(default="", description="紧急联系人电话")
    emergency_contact_relation: str = Field(default="", description="紧急联系人关系")
    notes: str = Field(default="", description="备注")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")


class PatientCreate(BaseModel):
    """创建患者。"""

    full_name: str = Field(min_length=1, description="姓名")
    gender: str = Field(default="", description="性别")
    birth_date: str | None = Field(default=None, description="出生日期")
    blood_type: str = Field(default="", description="血型")
    height_cm: float | None = Field(default=None, description="身高 cm")
    weight_kg: float | None = Field(default=None, description="体重 kg")
    phone: str = Field(default="", description="联系电话")
    relationship: str = Field(default="self", description="与本人关系")
    emergency_contact_name: str = Field(default="", description="紧急联系人姓名")
    emergency_contact_phone: str = Field(default="", description="紧急联系人电话")
    emergency_contact_relation: str = Field(default="", description="紧急联系人关系")
    notes: str = Field(default="", description="备注")


class PatientUpdate(BaseModel):
    """更新患者。"""

    full_name: str | None = None
    gender: str | None = None
    birth_date: str | None = None
    blood_type: str | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    phone: str | None = None
    relationship: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    emergency_contact_relation: str | None = None
    notes: str | None = None


# ── 就诊记录 ──────────────────────────────────────────────────


class Encounter(BaseModel):
    """就诊记录。"""

    id: int = Field(description="就诊记录 ID")
    patient_id: int = Field(description="关联患者 ID")
    encounter_date: str = Field(description="就诊日期 YYYY-MM-DD")
    hospital: str = Field(default="", description="医院")
    department: str = Field(default="", description="科室")
    doctor: str = Field(default="", description="医生")
    encounter_type: EncounterType = Field(default=EncounterType.OUTPATIENT, description="就诊类型")
    priority: EncounterPriority = Field(default=EncounterPriority.ROUTINE, description="优先级")
    chief_complaint: str = Field(default="", description="主诉")
    present_illness: str = Field(default="", description="现病史")
    physical_exam: str = Field(default="", description="体格检查")
    diagnosis: str = Field(default="", description="诊断")
    treatment_plan: str = Field(default="", description="治疗计划/处理意见")
    follow_up_instructions: str = Field(default="", description="随访指示")
    location: str = Field(default="", description="就诊地点")
    cost_yuan: float | None = Field(default=None, description="费用（元）")
    notes: str = Field(default="", description="备注")
    tags: list[str] = Field(default_factory=list, description="标签")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")


class EncounterCreate(BaseModel):
    """创建就诊记录。"""

    patient_id: int
    encounter_date: str
    hospital: str = ""
    department: str = ""
    doctor: str = ""
    encounter_type: EncounterType = EncounterType.OUTPATIENT
    priority: EncounterPriority = EncounterPriority.ROUTINE
    chief_complaint: str = ""
    present_illness: str = ""
    physical_exam: str = ""
    diagnosis: str = ""
    treatment_plan: str = ""
    follow_up_instructions: str = ""
    location: str = ""
    cost_yuan: float | None = None
    notes: str = ""
    tags: list[str] = Field(default_factory=list)


class EncounterUpdate(BaseModel):
    """更新就诊记录。"""

    patient_id: int | None = None
    encounter_date: str | None = None
    hospital: str | None = None
    department: str | None = None
    doctor: str | None = None
    encounter_type: EncounterType | None = None
    priority: EncounterPriority | None = None
    chief_complaint: str | None = None
    present_illness: str | None = None
    physical_exam: str | None = None
    diagnosis: str | None = None
    treatment_plan: str | None = None
    follow_up_instructions: str | None = None
    location: str | None = None
    cost_yuan: float | None = None
    notes: str | None = None
    tags: list[str] | None = None


# ── 健康问题 / 疾病 ───────────────────────────────────────────


class Condition(BaseModel):
    """健康问题/疾病。"""

    id: int = Field(description="健康问题 ID")
    patient_id: int = Field(description="关联患者 ID")
    condition_name: str = Field(description="问题名称，如 胆总管结石")
    diagnosis: str = Field(default="", description="详细诊断")
    status: ConditionStatus = Field(default=ConditionStatus.ACTIVE, description="状态")
    severity: ConditionSeverity | None = Field(default=None, description="严重程度")
    onset_date: str | None = Field(default=None, description="发病/发现日期")
    resolved_date: str | None = Field(default=None, description="缓解/治愈日期")
    icd10_code: str = Field(default="", description="ICD-10 编码")
    notes: str = Field(default="", description="备注")
    tags: list[str] = Field(default_factory=list, description="标签")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")


class ConditionCreate(BaseModel):
    """创建健康问题。"""

    patient_id: int
    condition_name: str = Field(min_length=1)
    diagnosis: str = ""
    status: ConditionStatus = ConditionStatus.ACTIVE
    severity: ConditionSeverity | None = None
    onset_date: str | None = None
    resolved_date: str | None = None
    icd10_code: str = ""
    notes: str = ""
    tags: list[str] = Field(default_factory=list)


class ConditionUpdate(BaseModel):
    """更新健康问题。"""

    patient_id: int | None = None
    condition_name: str | None = None
    diagnosis: str | None = None
    status: ConditionStatus | None = None
    severity: ConditionSeverity | None = None
    onset_date: str | None = None
    resolved_date: str | None = None
    icd10_code: str | None = None
    notes: str | None = None
    tags: list[str] | None = None


# ── 用药记录 ──────────────────────────────────────────────────


class Medication(BaseModel):
    """用药记录。"""

    id: int = Field(description="用药记录 ID")
    patient_id: int = Field(description="关联患者 ID")
    medication_name: str = Field(description="药品名称")
    medication_type: MedicationType = Field(
        default=MedicationType.PRESCRIPTION, description="药物类型"
    )
    dosage: str = Field(default="", description="剂量，如 50mg")
    frequency: str = Field(default="", description="频率，如 每日3次")
    route: str = Field(default="", description="给药途径，如 口服")
    indication: str = Field(default="", description="适应症")
    start_date: str | None = Field(default=None, description="开始日期")
    end_date: str | None = Field(default=None, description="结束日期")
    status: MedicationStatus = Field(default=MedicationStatus.ACTIVE, description="用药状态")
    prescribing_doctor: str = Field(default="", description="开方医生")
    pharmacy: str = Field(default="", description="药房")
    side_effects: str = Field(default="", description="副作用")
    notes: str = Field(default="", description="备注")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")


class MedicationCreate(BaseModel):
    """创建用药记录。"""

    patient_id: int
    medication_name: str = Field(min_length=1)
    medication_type: MedicationType = MedicationType.PRESCRIPTION
    dosage: str = ""
    frequency: str = ""
    route: str = ""
    indication: str = ""
    start_date: str | None = None
    end_date: str | None = None
    status: MedicationStatus = MedicationStatus.ACTIVE
    prescribing_doctor: str = ""
    pharmacy: str = ""
    side_effects: str = ""
    notes: str = ""


class MedicationUpdate(BaseModel):
    """更新用药记录。"""

    patient_id: int | None = None
    medication_name: str | None = None
    medication_type: MedicationType | None = None
    dosage: str | None = None
    frequency: str | None = None
    route: str | None = None
    indication: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    status: MedicationStatus | None = None
    prescribing_doctor: str | None = None
    pharmacy: str | None = None
    side_effects: str | None = None
    notes: str | None = None


# ── 化验结果 ──────────────────────────────────────────────────


class LabTestComponent(BaseModel):
    """单个化验项目明细。"""

    id: int = Field(description="明细 ID")
    lab_result_id: int = Field(description="关联化验主表 ID")
    test_name: str = Field(description="项目名称，如 白细胞")
    abbreviation: str = Field(default="", description="缩写，如 WBC")
    value: float | None = Field(default=None, description="数值结果")
    unit: str = Field(default="", description="单位")
    qualitative_value: str = Field(default="", description="定性结果，如 阳性/阴性")
    ref_range_min: float | None = Field(default=None, description="参考范围下限")
    ref_range_max: float | None = Field(default=None, description="参考范围上限")
    ref_range_text: str = Field(default="", description="参考范围文本")
    status: LabComponentStatus = Field(default=LabComponentStatus.NORMAL, description="结果状态")
    category: str = Field(default="", description="分类，如 血常规/生化")
    notes: str = Field(default="", description="备注")


class LabTestComponentCreate(BaseModel):
    """创建化验明细。"""

    test_name: str = Field(min_length=1)
    abbreviation: str = ""
    value: float | None = None
    unit: str = ""
    qualitative_value: str = ""
    ref_range_min: float | None = None
    ref_range_max: float | None = None
    ref_range_text: str = ""
    status: LabComponentStatus = LabComponentStatus.NORMAL
    category: str = ""
    notes: str = ""


class LabResult(BaseModel):
    """化验结果主表。"""

    id: int = Field(description="化验结果 ID")
    patient_id: int = Field(description="关联患者 ID")
    encounter_id: int | None = Field(default=None, description="关联就诊记录 ID")
    test_name: str = Field(description="化验名称，如 血常规+超敏C反应蛋白")
    test_category: str = Field(default="", description="分类")
    facility: str = Field(default="", description="检测机构/医院")
    status: LabResultStatus = Field(default=LabResultStatus.COMPLETED, description="状态")
    ordered_date: str | None = Field(default=None, description="开单日期")
    completed_date: str | None = Field(default=None, description="报告日期")
    overall_interpretation: str = Field(default="", description="总体解读")
    notes: str = Field(default="", description="备注")
    tags: list[str] = Field(default_factory=list, description="标签")
    components: list[LabTestComponent] = Field(default_factory=list, description="化验项目明细")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")


class LabResultCreate(BaseModel):
    """创建化验结果。"""

    patient_id: int
    encounter_id: int | None = None
    test_name: str = Field(min_length=1)
    test_category: str = ""
    facility: str = ""
    status: LabResultStatus = LabResultStatus.COMPLETED
    ordered_date: str | None = None
    completed_date: str | None = None
    overall_interpretation: str = ""
    notes: str = ""
    tags: list[str] = Field(default_factory=list)
    components: list[LabTestComponentCreate] = Field(default_factory=list)


class LabResultUpdate(BaseModel):
    """更新化验结果。"""

    patient_id: int | None = None
    encounter_id: int | None = None
    test_name: str | None = None
    test_category: str | None = None
    facility: str | None = None
    status: LabResultStatus | None = None
    ordered_date: str | None = None
    completed_date: str | None = None
    overall_interpretation: str | None = None
    notes: str | None = None
    tags: list[str] | None = None


# ── 检查 / 手术 ───────────────────────────────────────────────


class Procedure(BaseModel):
    """检查/手术记录（CT、MRI、超声、内镜、手术等）。"""

    id: int = Field(description="检查记录 ID")
    patient_id: int = Field(description="关联患者 ID")
    encounter_id: int | None = Field(default=None, description="关联就诊记录 ID")
    procedure_name: str = Field(description="检查名称，如 上腹部CT平扫")
    procedure_type: ProcedureType = Field(default=ProcedureType.IMAGING, description="检查类型")
    body_part: str = Field(default="", description="检查部位")
    facility: str = Field(default="", description="医院/机构")
    doctor: str = Field(default="", description="操作/报告医生")
    procedure_date: str = Field(description="检查日期")
    status: ProcedureStatus = Field(default=ProcedureStatus.COMPLETED, description="状态")
    findings: str = Field(default="", description="影像学表现/检查所见")
    conclusion: str = Field(default="", description="诊断意见/结论")
    abnormal_summary: str = Field(default="", description="异常摘要")
    follow_up_recommendation: str = Field(default="", description="复查/进一步检查建议")
    needs_follow_up: bool = Field(default=False, description="是否需要复查")
    notes: str = Field(default="", description="备注")
    tags: list[str] = Field(default_factory=list, description="标签")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")


class ProcedureCreate(BaseModel):
    """创建检查记录。"""

    patient_id: int
    encounter_id: int | None = None
    procedure_name: str = Field(min_length=1)
    procedure_type: ProcedureType = ProcedureType.IMAGING
    body_part: str = ""
    facility: str = ""
    doctor: str = ""
    procedure_date: str
    status: ProcedureStatus = ProcedureStatus.COMPLETED
    findings: str = ""
    conclusion: str = ""
    abnormal_summary: str = ""
    follow_up_recommendation: str = ""
    needs_follow_up: bool = False
    notes: str = ""
    tags: list[str] = Field(default_factory=list)


class ProcedureUpdate(BaseModel):
    """更新检查记录。"""

    patient_id: int | None = None
    encounter_id: int | None = None
    procedure_name: str | None = None
    procedure_type: ProcedureType | None = None
    body_part: str | None = None
    facility: str | None = None
    doctor: str | None = None
    procedure_date: str | None = None
    status: ProcedureStatus | None = None
    findings: str | None = None
    conclusion: str | None = None
    abnormal_summary: str | None = None
    follow_up_recommendation: str | None = None
    needs_follow_up: bool | None = None
    notes: str | None = None
    tags: list[str] | None = None


# ── 过敏史 ────────────────────────────────────────────────────


class Allergy(BaseModel):
    """过敏史。"""

    id: int = Field(description="过敏记录 ID")
    patient_id: int = Field(description="关联患者 ID")
    allergen: str = Field(description="过敏原")
    reaction: str = Field(default="", description="过敏反应")
    severity: ConditionSeverity = Field(default=ConditionSeverity.MILD, description="严重程度")
    onset_date: str | None = Field(default=None, description="首次发现日期")
    status: AllergyStatus = Field(default=AllergyStatus.ACTIVE, description="状态")
    notes: str = Field(default="", description="备注")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")


class AllergyCreate(BaseModel):
    """创建过敏记录。"""

    patient_id: int
    allergen: str = Field(min_length=1)
    reaction: str = ""
    severity: ConditionSeverity = ConditionSeverity.MILD
    onset_date: str | None = None
    status: AllergyStatus = AllergyStatus.ACTIVE
    notes: str = ""


class AllergyUpdate(BaseModel):
    """更新过敏记录。"""

    patient_id: int | None = None
    allergen: str | None = None
    reaction: str | None = None
    severity: ConditionSeverity | None = None
    onset_date: str | None = None
    status: AllergyStatus | None = None
    notes: str | None = None


# ── 生命体征 ──────────────────────────────────────────────────


class Vitals(BaseModel):
    """生命体征记录。"""

    id: int = Field(description="记录 ID")
    patient_id: int = Field(description="关联患者 ID")
    recorded_date: str = Field(description="测量日期 YYYY-MM-DD")
    systolic_bp: int | None = Field(default=None, description="收缩压 mmHg")
    diastolic_bp: int | None = Field(default=None, description="舒张压 mmHg")
    heart_rate: int | None = Field(default=None, description="心率 bpm")
    temperature_c: float | None = Field(default=None, description="体温 ℃")
    weight_kg: float | None = Field(default=None, description="体重 kg")
    height_cm: float | None = Field(default=None, description="身高 cm")
    oxygen_saturation: float | None = Field(default=None, description="血氧饱和度 %")
    respiratory_rate: int | None = Field(default=None, description="呼吸频率 次/分")
    blood_glucose: float | None = Field(default=None, description="血糖 mmol/L")
    glucose_context: VitalGlucoseContext | None = Field(default=None, description="血糖测量上下文")
    pain_scale: int | None = Field(default=None, description="疼痛评分 0-10")
    notes: str = Field(default="", description="备注")
    created_at: datetime = Field(description="创建时间")


class VitalsCreate(BaseModel):
    """创建生命体征记录。"""

    patient_id: int
    recorded_date: str
    systolic_bp: int | None = None
    diastolic_bp: int | None = None
    heart_rate: int | None = None
    temperature_c: float | None = None
    weight_kg: float | None = None
    height_cm: float | None = None
    oxygen_saturation: float | None = None
    respiratory_rate: int | None = None
    blood_glucose: float | None = None
    glucose_context: VitalGlucoseContext | None = None
    pain_scale: int | None = None
    notes: str = ""


# ── 疫苗接种 ──────────────────────────────────────────────────


class Immunization(BaseModel):
    """疫苗接种记录。"""

    id: int = Field(description="记录 ID")
    patient_id: int = Field(description="关联患者 ID")
    vaccine_name: str = Field(description="疫苗名称")
    date_administered: str = Field(description="接种日期")
    dose_number: int | None = Field(default=None, description="剂次")
    manufacturer: str = Field(default="", description="制造商")
    lot_number: str = Field(default="", description="批号")
    site: str = Field(default="", description="接种部位")
    facility: str = Field(default="", description="接种机构")
    administering_person: str = Field(default="", description="接种人")
    notes: str = Field(default="", description="备注")
    created_at: datetime = Field(description="创建时间")


class ImmunizationCreate(BaseModel):
    """创建疫苗接种记录。"""

    patient_id: int
    vaccine_name: str = Field(min_length=1)
    date_administered: str
    dose_number: int | None = None
    manufacturer: str = ""
    lot_number: str = ""
    site: str = ""
    facility: str = ""
    administering_person: str = ""
    notes: str = ""


# ── 医生信息 ──────────────────────────────────────────────────


class Doctor(BaseModel):
    """医生/医疗机构信息。"""

    id: int = Field(description="医生 ID")
    name: str = Field(description="医生姓名")
    title: str = Field(default="", description="职称，如 主任医师、副主任医师")
    specialty: str = Field(default="", description="专科，如 肝胆胰外科")
    hospital: str = Field(default="", description="所属医院")
    department: str = Field(default="", description="科室")
    phone: str = Field(default="", description="联系电话")
    address: str = Field(default="", description="地址")
    notes: str = Field(default="", description="备注，如 就诊体验、擅长领域")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")


class DoctorCreate(BaseModel):
    """创建医生信息。"""

    name: str = Field(min_length=1)
    title: str = ""
    specialty: str = ""
    hospital: str = ""
    department: str = ""
    phone: str = ""
    address: str = ""
    notes: str = ""


class DoctorUpdate(BaseModel):
    """更新医生信息。"""

    name: str | None = None
    title: str | None = None
    specialty: str | None = None
    hospital: str | None = None
    department: str | None = None
    phone: str | None = None
    address: str | None = None
    notes: str | None = None


# ── 文档 / 附件 ───────────────────────────────────────────────


class DocumentType(StrEnum):
    """文档类型。"""

    REPORT = "report"  # 检查/化验报告
    PRESCRIPTION = "prescription"  # 处方
    MEDICAL_RECORD = "medical_record"  # 病历
    CERTIFICATE = "certificate"  # 诊断证明
    BILL = "bill"  # 费用单据
    IMAGING = "imaging"  # 影像资料
    OTHER = "other"


class HealthDocument(BaseModel):
    """健康文档/附件，存储看病资料原件。"""

    id: int = Field(description="文档 ID")
    patient_id: int = Field(description="关联患者 ID")
    encounter_id: int | None = Field(default=None, description="关联就诊记录 ID")
    title: str = Field(description="文档标题")
    document_type: DocumentType = Field(default=DocumentType.OTHER, description="文档类型")
    file_name: str = Field(default="", description="原始文件名")
    file_path: str = Field(default="", description="存储路径")
    file_size: int = Field(default=0, description="文件大小（字节）")
    mime_type: str = Field(default="", description="MIME 类型")
    document_date: str | None = Field(default=None, description="文档日期")
    hospital: str = Field(default="", description="出具医院")
    summary: str = Field(default="", description="内容摘要/AI解读")
    tags: list[str] = Field(default_factory=list, description="标签")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")


class HealthDocumentCreate(BaseModel):
    """创建文档记录。"""

    patient_id: int
    encounter_id: int | None = None
    title: str = Field(min_length=1)
    document_type: DocumentType = DocumentType.OTHER
    file_name: str = ""
    file_path: str = ""
    file_size: int = 0
    mime_type: str = ""
    document_date: str | None = None
    hospital: str = ""
    summary: str = ""
    tags: list[str] = Field(default_factory=list)


class HealthDocumentUpdate(BaseModel):
    """更新文档记录。"""

    patient_id: int | None = None
    encounter_id: int | None = None
    title: str | None = None
    document_type: DocumentType | None = None
    file_name: str | None = None
    file_path: str | None = None
    file_size: int | None = None
    mime_type: str | None = None
    document_date: str | None = None
    hospital: str | None = None
    summary: str | None = None
    tags: list[str] | None = None


# ── 预约 / 复诊 ────────────────────────────────────────────────


class AppointmentStatus(StrEnum):
    """预约状态。"""

    SCHEDULED = "scheduled"  # 已预约
    CONFIRMED = "confirmed"  # 已确认
    COMPLETED = "completed"  # 已完成
    CANCELLED = "cancelled"  # 已取消
    NO_SHOW = "no_show"  # 未就诊


class AppointmentType(StrEnum):
    """预约类型。"""

    FOLLOW_UP = "follow_up"  # 复诊
    CONSULTATION = "consultation"  # 咨询
    CHECKUP = "checkup"  # 体检
    PROCEDURE = "procedure"  # 检查/治疗
    VACCINATION = "vaccination"  # 疫苗接种
    OTHER = "other"


class Appointment(BaseModel):
    """预约/复诊记录。"""

    id: int = Field(description="预约 ID")
    patient_id: int = Field(description="关联患者 ID")
    doctor_id: int | None = Field(default=None, description="关联医生 ID")
    title: str = Field(description="预约标题")
    appointment_type: AppointmentType = Field(
        default=AppointmentType.CONSULTATION, description="预约类型"
    )
    status: AppointmentStatus = Field(default=AppointmentStatus.SCHEDULED, description="状态")
    scheduled_date: str = Field(description="预约日期 YYYY-MM-DD")
    scheduled_time: str = Field(default="", description="预约时间 HH:MM")
    hospital: str = Field(default="", description="医院")
    department: str = Field(default="", description="科室")
    doctor_name: str = Field(default="", description="医生姓名")
    reason: str = Field(default="", description="就诊原因")
    notes: str = Field(default="", description="备注")
    reminder_enabled: bool = Field(default=True, description="是否开启提醒")
    reminder_days_before: int = Field(default=1, description="提前几天提醒")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")


class AppointmentCreate(BaseModel):
    """创建预约。"""

    patient_id: int
    doctor_id: int | None = None
    title: str = Field(min_length=1)
    appointment_type: AppointmentType = AppointmentType.CONSULTATION
    status: AppointmentStatus = AppointmentStatus.SCHEDULED
    scheduled_date: str
    scheduled_time: str = ""
    hospital: str = ""
    department: str = ""
    doctor_name: str = ""
    reason: str = ""
    notes: str = ""
    reminder_enabled: bool = True
    reminder_days_before: int = 1


class AppointmentUpdate(BaseModel):
    """更新预约。"""

    patient_id: int | None = None
    doctor_id: int | None = None
    title: str | None = None
    appointment_type: AppointmentType | None = None
    status: AppointmentStatus | None = None
    scheduled_date: str | None = None
    scheduled_time: str | None = None
    hospital: str | None = None
    department: str | None = None
    doctor_name: str | None = None
    reason: str | None = None
    notes: str | None = None
    reminder_enabled: bool | None = None
    reminder_days_before: int | None = None


# ── 服药记录 ──────────────────────────────────────────────────


class MedicationLogStatus(StrEnum):
    """服药记录状态。"""

    TAKEN = "taken"  # 已服用
    MISSED = "missed"  # 漏服
    SKIPPED = "skipped"  # 跳过（故意不服）
    LATE = "late"  # 延迟服用


class MedicationLog(BaseModel):
    """服药记录，用于追踪用药依从性。"""

    id: int = Field(description="记录 ID")
    patient_id: int = Field(description="关联患者 ID")
    medication_id: int | None = Field(default=None, description="关联用药 ID")
    medication_name: str = Field(description="药物名称")
    dosage: str = Field(default="", description="剂量")
    scheduled_date: str = Field(description="计划服药日期")
    scheduled_time: str = Field(default="", description="计划服药时间")
    taken_at: str | None = Field(default=None, description="实际服用时间")
    status: MedicationLogStatus = Field(default=MedicationLogStatus.TAKEN, description="状态")
    notes: str = Field(default="", description="备注")
    created_at: datetime = Field(description="创建时间")


class MedicationLogCreate(BaseModel):
    """创建服药记录。"""

    patient_id: int
    medication_id: int | None = None
    medication_name: str = Field(min_length=1)
    dosage: str = ""
    scheduled_date: str
    scheduled_time: str = ""
    taken_at: str | None = None
    status: MedicationLogStatus = MedicationLogStatus.TAKEN
    notes: str = ""


# ── 药物相互作用 ──────────────────────────────────────────────


class DrugInteraction(BaseModel):
    """药物相互作用检查结果。"""

    drug_a: str = Field(description="药物A")
    drug_b: str = Field(description="药物B")
    severity: str = Field(description="严重程度：major/moderate/minor")
    description: str = Field(description="相互作用说明")


# ── 时间线事件 ────────────────────────────────────────────────


class TimelineEvent(BaseModel):
    """健康时间线事件，用于按时间聚合展示所有健康事件。"""

    id: int = Field(description="事件 ID")
    date: str = Field(description="事件日期")
    event_type: str = Field(
        description="事件类型：encounter/procedure/lab/medication/condition/document/vitals/immunization"
    )
    title: str = Field(description="事件标题")
    description: str = Field(default="", description="事件描述")
    status: str = Field(default="", description="状态标签")
    severity: str = Field(default="", description="严重程度（如有）")
    related_id: int = Field(description="关联记录 ID")


# ── AI 健康洞察 ───────────────────────────────────────────────


class HealthInsight(BaseModel):
    """AI 生成的健康洞察/报告解读。"""

    id: int = Field(description="洞察 ID")
    patient_id: int = Field(description="关联患者 ID")
    target_type: str = Field(description="目标类型：lab_result/procedure/encounter/summary")
    target_id: int = Field(description="目标记录 ID")
    insight_type: str = Field(
        default="interpretation", description="洞察类型：interpretation/trend/summary/warning"
    )
    content: str = Field(description="洞察内容")
    model: str = Field(default="", description="使用的模型")
    created_at: datetime = Field(description="创建时间")


class HealthInsightCreate(BaseModel):
    """创建 AI 洞察。"""

    patient_id: int
    target_type: str
    target_id: int
    insight_type: str = "interpretation"
    content: str
    model: str = ""


# ── 统计 ──────────────────────────────────────────────────────


class HealthStats(BaseModel):
    """健康档案统计信息。"""

    total_patients: int = 0
    total_encounters: int = 0
    total_conditions: int = 0
    active_conditions: int = 0
    total_medications: int = 0
    active_medications: int = 0
    total_lab_results: int = 0
    total_procedures: int = 0
    total_allergies: int = 0
    total_vitals: int = 0
    total_immunizations: int = 0
    total_doctors: int = 0
    total_documents: int = 0
    total_insights: int = 0
    total_appointments: int = 0
    upcoming_appointments: int = 0
    total_medication_logs: int = 0
    medication_adherence_rate: float = 0.0
    pending_follow_ups: int = 0
    earliest_encounter_date: str | None = None
    latest_encounter_date: str | None = None
