// ── 健康档案页（从独立 /health 迁移进桌面应用，沿用应用主色调，内部表格/表单）──

(function () {
  'use strict';

  var API = '/api/health';
  var currentPatientId = null;
  var stats = null;
  var initialized = false;

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  async function api(path, opts) {
    opts = opts || {};
    var res = await fetch(API + path, { headers: { 'Content-Type': 'application/json' }, ...opts });
    return res.json();
  }

  function fmtDate(d) { return d ? d.slice(0, 10) : '-'; }

  function badge(type, val) {
    if (val == null) return '-';
    var map = {
      active: 'badge-active', inactive: 'badge-inactive', resolved: 'badge-resolved', chronic: 'badge-chronic',
      high: 'badge-high', low: 'badge-low', normal: 'badge-normal',
      outpatient: 'badge-normal', emergency: 'badge-high',
    };
    return '<span class="badge ' + (map[val] || 'badge-inactive') + '">' + esc(val) + '</span>';
  }

  function modal(title, body) {
    $('healthModalContent').innerHTML = '<h3 class="health-modal-title">' + title + '</h3>' + body;
    $('healthModalOverlay').classList.add('show');
  }
  window.closeModal = function () { $('healthModalOverlay').classList.remove('show'); };

  async function renderTab(tab) {
    var el = $('healthContent');
    if (!el) return;
    el.innerHTML = '<div class="health-empty">加载中...</div>';
    switch (tab) {
      case 'overview': await renderOverview(); break;
      case 'encounters': await renderEncounters(); break;
      case 'conditions': await renderConditions(); break;
      case 'medications': await renderMedications(); break;
      case 'labs': await renderLabs(); break;
      case 'procedures': await renderProcedures(); break;
      case 'timeline': await renderTimeline(); break;
      case 'documents': await renderDocuments(); break;
      case 'doctors': await renderDoctors(); break;
      case 'appointments': await renderAppointments(); break;
      case 'medication-logs': await renderMedicationLogs(); break;
      default: await renderOverview(); break;
    }
  }

  // ── Overview ──
  async function renderOverview() {
    var el = $('healthContent');
    var [statsRes, patientsRes] = await Promise.all([api('/stats'), api('/patients')]);
    stats = statsRes.stats;
    var patients = patientsRes.items || [];
    if (patients.length > 0 && !currentPatientId) currentPatientId = patients[0].id;

    var html = '<div class="stats-grid">';
    function statCard(label, value, cls) {
      return '<div class="stat-card"><div class="stat-value ' + (cls || '') + '">' + value + '</div><div class="stat-label">' + esc(label) + '</div></div>';
    }
    html += statCard('就诊总次数', stats.total_encounters);
    html += statCard('活跃健康问题', stats.active_conditions, 'warning');
    html += statCard('当前用药', stats.active_medications, 'success');
    html += statCard('化验报告', stats.total_lab_results);
    html += statCard('检查记录', stats.total_procedures);
    html += statCard('待复查', stats.pending_follow_ups, 'danger');
    html += statCard('文档资料', stats.total_documents || 0);
    html += statCard('医生信息', stats.total_doctors || 0);
    html += statCard('即将预约', stats.upcoming_appointments || 0);
    html += statCard('服药记录', stats.total_medication_logs || 0);
    html += '</div>';

    if (patients.length > 1) {
      html += '<div class="health-card"><h2>选择档案成员</h2><div class="health-members">';
      patients.forEach(function (p) {
        html += '<button class="pill-btn' + (p.id === currentPatientId ? ' dark' : '') + '" onclick="selectPatient(' + p.id + ')">' + esc(p.full_name) + (p.relationship !== 'self' ? '(' + esc(p.relationship) + ')' : '') + '</button>';
      });
      html += '</div></div>';
    }

    if (currentPatientId) {
      var [condRes, medRes, procRes] = await Promise.all([
        api('/conditions?patient_id=' + currentPatientId + '&status=active'),
        api('/medications?patient_id=' + currentPatientId + '&status=active'),
        api('/procedures?patient_id=' + currentPatientId + '&needs_follow_up=true'),
      ]);
      html += '<div class="health-card"><h2>当前健康问题</h2>';
      if (condRes.items && condRes.items.length) {
        html += '<div class="table-wrap"><table><tr><th>问题</th><th>严重程度</th><th>发现日期</th><th>备注</th></tr>';
        condRes.items.forEach(function (c) {
          html += '<tr><td><strong>' + esc(c.condition_name) + '</strong><br><small class="health-sub">' + esc(c.diagnosis || '') + '</small></td><td>' + (c.severity ? badge('severity', c.severity) : '-') + '</td><td>' + fmtDate(c.onset_date) + '</td><td>' + esc(c.notes || '-') + '</td></tr>';
        });
        html += '</table></div>';
      } else html += '<div class="health-empty">暂无活跃健康问题</div>';
      html += '</div>';

      html += '<div class="health-card"><h2>当前用药</h2>';
      if (medRes.items && medRes.items.length) {
        html += '<div class="table-wrap"><table><tr><th>药品</th><th>剂量</th><th>频率</th><th>适应症</th></tr>';
        medRes.items.forEach(function (m) {
          html += '<tr><td><strong>' + esc(m.medication_name) + '</strong></td><td>' + esc(m.dosage || '-') + '</td><td>' + esc(m.frequency || '-') + '</td><td>' + esc(m.indication || '-') + '</td></tr>';
        });
        html += '</table></div>';
      } else html += '<div class="health-empty">暂无当前用药</div>';
      html += '</div>';

      if (procRes.items && procRes.items.length) {
        html += '<div class="health-card"><h2 class="health-danger-text">待复查提醒</h2><div class="table-wrap"><table><tr><th>检查</th><th>日期</th><th>复查建议</th></tr>';
        procRes.items.forEach(function (p) {
          html += '<tr><td><strong>' + esc(p.procedure_name) + '</strong></td><td>' + fmtDate(p.procedure_date) + '</td><td>' + esc(p.follow_up_recommendation || '建议复查') + '</td></tr>';
        });
        html += '</table></div></div>';
      }
    }
    el.innerHTML = html;
  }

  window.selectPatient = function (id) { currentPatientId = id; renderTab('overview'); };

  // ── Encounters ──
  async function renderEncounters() {
    var el = $('healthContent');
    var res = await api('/encounters' + (currentPatientId ? '?patient_id=' + currentPatientId : '') + '&limit=200');
    var html = '<div class="health-card"><h2>就诊记录 <button class="pill-btn dark health-sm" onclick="showEncounterForm()">+ 新增就诊</button></h2>';
    if (res.items && res.items.length) {
      html += '<div class="table-wrap"><table><tr><th>日期</th><th>医院</th><th>科室</th><th>类型</th><th>主诉</th><th>诊断</th><th>医生</th><th>操作</th></tr>';
      res.items.forEach(function (e) {
        html += '<tr><td>' + fmtDate(e.encounter_date) + '</td><td>' + esc(e.hospital || '-') + '</td><td>' + esc(e.department || '-') + '</td><td>' + badge('type', e.encounter_type) + '</td><td>' + esc(e.chief_complaint || '-') + '</td><td>' + esc(e.diagnosis || '-') + '</td><td>' + esc(e.doctor || '-') + '</td><td><button class="pill-btn health-sm" onclick="viewEncounter(' + e.id + ')">详情</button></td></tr>';
      });
      html += '</table></div>';
    } else html += '<div class="health-empty">暂无就诊记录</div>';
    html += '</div>';
    el.innerHTML = html;
  }

  window.viewEncounter = async function (id) {
    var res = await api('/encounters/' + id);
    var e = res.data;
    modal('就诊详情 — ' + fmtDate(e.encounter_date),
      rows([
        ['日期', fmtDate(e.encounter_date)],
        ['医院', e.hospital], ['科室', e.department], ['医生', e.doctor],
        ['类型', e.encounter_type], ['主诉', e.chief_complaint], ['现病史', e.present_illness],
        ['体格检查', e.physical_exam], ['诊断', e.diagnosis ? '<strong>' + esc(e.diagnosis) + '</strong>' : '-'],
        ['治疗计划', e.treatment_plan], ['随访指示', e.follow_up_instructions],
        ['费用', e.cost_yuan != null ? '¥' + e.cost_yuan : '-'], ['备注', e.notes],
      ]) +
      '<div class="modal-actions"><button class="pill-btn health-danger" onclick="deleteItem(\'encounters\',' + e.id + ')">删除</button><button class="pill-btn" onclick="closeModal()">关闭</button></div>');
  };

  window.showEncounterForm = function () {
    modal('新增就诊记录', formField('日期 *', 'date', 'f_date', new Date().toISOString().slice(0, 10)) +
      '<div class="form-row">' + formField('医院', 'text', 'f_hospital') + formField('科室', 'text', 'f_dept') + '</div>' +
      '<div class="form-row">' + formField('医生', 'text', 'f_doctor') + formField('费用(元)', 'number', 'f_cost') + '</div>' +
      formField('主诉', 'text', 'f_complaint') + formArea('现病史', 'f_illness') + formArea('诊断', 'f_diagnosis') +
      formArea('治疗计划', 'f_plan') + formField('随访指示', 'text', 'f_followup') +
      '<div class="modal-actions"><button class="pill-btn" onclick="closeModal()">取消</button><button class="pill-btn dark" onclick="submitEncounter()">保存</button></div>');
  };

  window.submitEncounter = async function () {
    await api('/encounters', { method: 'POST', body: JSON.stringify({
      patient_id: currentPatientId || 1,
      encounter_date: v('f_date'), encounter_type: v('f_type'),
      hospital: v('f_hospital'), department: v('f_dept'), doctor: v('f_doctor'),
      cost_yuan: parseFloat(v('f_cost')) || null,
      chief_complaint: v('f_complaint'), present_illness: v('f_illness'),
      diagnosis: v('f_diagnosis'), treatment_plan: v('f_plan'), follow_up_instructions: v('f_followup'),
    }) });
    closeModal(); renderTab('encounters');
  };

  // ── Conditions ──
  async function renderConditions() {
    var el = $('healthContent');
    var res = await api('/conditions' + (currentPatientId ? '?patient_id=' + currentPatientId : '') + '&limit=200');
    var html = '<div class="health-card"><h2>健康问题 <button class="pill-btn dark health-sm" onclick="showConditionForm()">+ 新增问题</button></h2>';
    if (res.items && res.items.length) {
      html += '<div class="table-wrap"><table><tr><th>问题</th><th>状态</th><th>严重程度</th><th>发现日期</th><th>缓解日期</th><th>操作</th></tr>';
      res.items.forEach(function (c) {
        html += '<tr><td><strong>' + esc(c.condition_name) + '</strong><br><small class="health-sub">' + esc(c.diagnosis || '') + '</small></td><td>' + badge('status', c.status) + '</td><td>' + (c.severity ? badge('severity', c.severity) : '-') + '</td><td>' + fmtDate(c.onset_date) + '</td><td>' + fmtDate(c.resolved_date) + '</td><td><button class="pill-btn health-sm health-danger" onclick="deleteItem(\'conditions\',' + c.id + ')">删除</button></td></tr>';
      });
      html += '</table></div>';
    } else html += '<div class="health-empty">暂无健康问题记录</div>';
    html += '</div>';
    el.innerHTML = html;
  }

  window.showConditionForm = function () {
    modal('新增健康问题', formField('问题名称 *', 'text', 'f_name') +
      '<div class="form-row"><div class="form-group"><label>状态</label><select id="f_status"><option value="active">活跃</option><option value="chronic">慢性</option><option value="resolved">已缓解</option><option value="inactive">不活跃</option></select></div>' +
      '<div class="form-group"><label>严重程度</label><select id="f_severity"><option value="">-</option><option value="mild">轻度</option><option value="moderate">中度</option><option value="severe">重度</option><option value="critical">危重</option></select></div></div>' +
      '<div class="form-row">' + formField('发现日期', 'date', 'f_onset') + formField('ICD-10编码', 'text', 'f_icd') + '</div>' +
      formArea('详细诊断', 'f_diag') + formArea('备注', 'f_notes') +
      '<div class="modal-actions"><button class="pill-btn" onclick="closeModal()">取消</button><button class="pill-btn dark" onclick="submitCondition()">保存</button></div>');
  };

  window.submitCondition = async function () {
    var sev = v('f_severity');
    await api('/conditions', { method: 'POST', body: JSON.stringify({
      patient_id: currentPatientId || 1, condition_name: v('f_name'),
      status: v('f_status'), severity: sev || null, onset_date: v('f_onset') || null,
      icd10_code: v('f_icd'), diagnosis: v('f_diag'), notes: v('f_notes'),
    }) });
    closeModal(); renderTab('conditions');
  };

  // ── Medications ──
  async function renderMedications() {
    var el = $('healthContent');
    var res = await api('/medications' + (currentPatientId ? '?patient_id=' + currentPatientId : '') + '&limit=200');
    var html = '<div class="health-card"><h2>用药记录 <button class="pill-btn dark health-sm" onclick="showMedicationForm()">+ 新增用药</button></h2>';
    if (res.items && res.items.length) {
      html += '<div class="table-wrap"><table><tr><th>药品</th><th>类型</th><th>剂量</th><th>频率</th><th>途径</th><th>状态</th><th>开始</th><th>操作</th></tr>';
      res.items.forEach(function (m) {
        html += '<tr><td><strong>' + esc(m.medication_name) + '</strong></td><td>' + esc(m.medication_type) + '</td><td>' + esc(m.dosage || '-') + '</td><td>' + esc(m.frequency || '-') + '</td><td>' + esc(m.route || '-') + '</td><td>' + badge('status', m.status) + '</td><td>' + fmtDate(m.start_date) + '</td><td><button class="pill-btn health-sm health-danger" onclick="deleteItem(\'medications\',' + m.id + ')">删除</button></td></tr>';
      });
      html += '</table></div>';
    } else html += '<div class="health-empty">暂无用药记录</div>';
    html += '</div>';
    el.innerHTML = html;
  }

  window.showMedicationForm = function () {
    modal('新增用药记录', formField('药品名称 *', 'text', 'f_name') +
      '<div class="form-row"><div class="form-group"><label>类型</label><select id="f_type"><option value="prescription">处方药</option><option value="otc">非处方药</option><option value="supplement">保健品</option><option value="herbal">中药</option></select></div>' +
      '<div class="form-group"><label>状态</label><select id="f_status"><option value="active">使用中</option><option value="completed">已完成</option><option value="stopped">已停用</option><option value="on_hold">暂停</option></select></div></div>' +
      '<div class="form-row">' + formField('剂量', 'text', 'f_dosage', '如 50mg') + formField('频率', 'text', 'f_freq', '如 每日3次') + '</div>' +
      '<div class="form-row">' + formField('给药途径', 'text', 'f_route', '如 口服') + formField('适应症', 'text', 'f_indication') + '</div>' +
      '<div class="form-row">' + formField('开始日期', 'date', 'f_start') + formField('结束日期', 'date', 'f_end') + '</div>' +
      formField('开方医生', 'text', 'f_doctor') + formArea('备注', 'f_notes') +
      '<div class="modal-actions"><button class="pill-btn" onclick="closeModal()">取消</button><button class="pill-btn dark" onclick="submitMedication()">保存</button></div>');
  };

  window.submitMedication = async function () {
    await api('/medications', { method: 'POST', body: JSON.stringify({
      patient_id: currentPatientId || 1, medication_name: v('f_name'),
      medication_type: v('f_type'), dosage: v('f_dosage'), frequency: v('f_freq'), route: v('f_route'),
      indication: v('f_indication'), start_date: v('f_start') || null, end_date: v('f_end') || null,
      status: v('f_status'), prescribing_doctor: v('f_doctor'), notes: v('f_notes'),
    }) });
    closeModal(); renderTab('medications');
  };

  // ── Lab Results ──
  async function renderLabs() {
    var el = $('healthContent');
    var res = await api('/lab-results' + (currentPatientId ? '?patient_id=' + currentPatientId : '') + '&limit=200');
    var html = '<div class="health-card"><h2>化验结果 <button class="pill-btn dark health-sm" onclick="showLabForm()">+ 新增化验</button></h2>';
    if (res.items && res.items.length) {
      html += '<div class="table-wrap"><table><tr><th>化验名称</th><th>机构</th><th>报告日期</th><th>项目数</th><th>异常数</th><th>操作</th></tr>';
      res.items.forEach(function (l) {
        var abnormal = (l.components || []).filter(function (c) { return c.status === 'high' || c.status === 'low' || c.status === 'critical'; }).length;
        html += '<tr><td><strong>' + esc(l.test_name) + '</strong></td><td>' + esc(l.facility || '-') + '</td><td>' + fmtDate(l.completed_date) + '</td><td>' + (l.components || []).length + '</td><td>' + (abnormal > 0 ? '<span class="health-danger-text">' + abnormal + '</span>' : '0') + '</td><td><button class="pill-btn health-sm" onclick="viewLab(' + l.id + ')">详情</button></td></tr>';
      });
      html += '</table></div>';
    } else html += '<div class="health-empty">暂无化验记录</div>';
    html += '</div>';
    el.innerHTML = html;
  }

  window.viewLab = async function (id) {
    var res = await api('/lab-results/' + id);
    var l = res.data;
    var rows = '';
    (l.components || []).forEach(function (c) {
      var abn = c.status === 'high' || c.status === 'low' || c.status === 'critical';
      rows += '<tr class="' + (abn ? 'abnormal' : '') + '"><td>' + esc(c.test_name) + (c.abbreviation ? ' <small class="health-sub">(' + esc(c.abbreviation) + ')</small>' : '') + '</td><td>' + esc(c.value != null ? c.value : (c.qualitative_value || '-')) + '</td><td>' + esc(c.unit || '-') + '</td><td>' + esc(c.ref_range_text || (c.ref_range_min != null ? c.ref_range_min + '~' + c.ref_range_max : '-')) + '</td><td>' + badge('lab', c.status) + '</td></tr>';
    });
    modal('化验详情 — ' + esc(l.test_name), rows([['机构', l.facility], ['报告日期', fmtDate(l.completed_date)], ['总体解读', l.overall_interpretation]]) +
      '<table class="lab-table"><tr><th>项目</th><th>结果</th><th>单位</th><th>参考范围</th><th>状态</th></tr>' + (rows || '<tr><td colspan="5" class="health-empty">无明细</td></tr>') + '</table>' +
      '<div class="modal-actions"><button class="pill-btn health-danger" onclick="deleteItem(\'lab-results\',' + l.id + ')">删除</button><button class="pill-btn" onclick="closeModal()">关闭</button></div>');
  };

  window.showLabForm = function () {
    modal('新增化验结果', formField('化验名称 *', 'text', 'f_name', '如 血常规+超敏C反应蛋白') +
      '<div class="form-row">' + formField('机构', 'text', 'f_facility') + formField('报告日期', 'date', 'f_date', new Date().toISOString().slice(0, 10)) + '</div>' +
      formField('分类', 'text', 'f_category', '如 血常规、生化') + formArea('总体解读', 'f_interp') +
      '<div class="form-group"><label>化验项目明细（每行：项目名|结果|单位|下限|上限|状态normal/high/low）</label><textarea id="f_components" rows="6" placeholder="白细胞|6.5|10^9/L|3.5|9.5|normal&#10;葡萄糖|6.7|mmol/L|3.9|6.1|high"></textarea></div>' +
      '<div class="modal-actions"><button class="pill-btn" onclick="closeModal()">取消</button><button class="pill-btn dark" onclick="submitLab()">保存</button></div>');
  };

  window.submitLab = async function () {
    var components = [];
    v('f_components').split('\n').forEach(function (line) {
      var parts = line.trim().split('|');
      if (parts.length >= 2 && parts[0]) {
        components.push({
          test_name: parts[0].trim(), value: parts[1] ? parseFloat(parts[1]) : null,
          unit: (parts[2] || '').trim(), ref_range_min: parts[3] ? parseFloat(parts[3]) : null,
          ref_range_max: parts[4] ? parseFloat(parts[4]) : null, status: (parts[5] || 'normal').trim(),
        });
      }
    });
    await api('/lab-results', { method: 'POST', body: JSON.stringify({
      patient_id: currentPatientId || 1, test_name: v('f_name'), facility: v('f_facility'),
      test_category: v('f_category'), completed_date: v('f_date'),
      overall_interpretation: v('f_interp'), components: components,
    }) });
    closeModal(); renderTab('labs');
  };

  // ── Procedures ──
  async function renderProcedures() {
    var el = $('healthContent');
    var res = await api('/procedures' + (currentPatientId ? '?patient_id=' + currentPatientId : '') + '&limit=200');
    var html = '<div class="health-card"><h2>检查记录 <button class="pill-btn dark health-sm" onclick="showProcedureForm()">+ 新增检查</button></h2>';
    if (res.items && res.items.length) {
      html += '<div class="table-wrap"><table><tr><th>检查名称</th><th>类型</th><th>部位</th><th>日期</th><th>机构</th><th>需复查</th><th>操作</th></tr>';
      res.items.forEach(function (p) {
        html += '<tr><td><strong>' + esc(p.procedure_name) + '</strong></td><td>' + esc(p.procedure_type) + '</td><td>' + esc(p.body_part || '-') + '</td><td>' + fmtDate(p.procedure_date) + '</td><td>' + esc(p.facility || '-') + '</td><td>' + (p.needs_follow_up ? '<span class="health-danger-text">是</span>' : '否') + '</td><td><button class="pill-btn health-sm" onclick="viewProcedure(' + p.id + ')">详情</button></td></tr>';
      });
      html += '</table></div>';
    } else html += '<div class="health-empty">暂无检查记录</div>';
    html += '</div>';
    el.innerHTML = html;
  }

  window.viewProcedure = async function (id) {
    var res = await api('/procedures/' + id);
    var p = res.data;
    modal('检查详情 — ' + esc(p.procedure_name),
      rows([['类型', p.procedure_type], ['部位', p.body_part], ['日期', fmtDate(p.procedure_date)], ['机构', p.facility], ['医生', p.doctor], ['检查所见', p.findings], ['诊断结论', p.conclusion ? '<strong>' + esc(p.conclusion) + '</strong>' : '-'], ['异常摘要', p.abnormal_summary], ['复查建议', p.follow_up_recommendation]]) +
      '<div class="modal-actions"><button class="pill-btn health-danger" onclick="deleteItem(\'procedures\',' + p.id + ')">删除</button><button class="pill-btn" onclick="closeModal()">关闭</button></div>');
  };

  window.showProcedureForm = function () {
    modal('新增检查记录', formField('检查名称 *', 'text', 'f_name', '如 上腹部CT平扫') +
      '<div class="form-row"><div class="form-group"><label>类型</label><select id="f_type"><option value="imaging">影像检查</option><option value="lab">化验</option><option value="endoscopy">内镜</option><option value="surgery">手术</option><option value="ecg">心电图</option><option value="other">其他</option></select></div>' +
      formField('部位', 'text', 'f_part', '如 上腹部') + '</div>' +
      '<div class="form-row">' + formField('机构', 'text', 'f_facility') + formField('日期', 'date', 'f_date', new Date().toISOString().slice(0, 10)) + '</div>' +
      formArea('检查所见', 'f_findings') + formArea('诊断结论', 'f_conclusion') +
      '<div class="form-row">' + formField('异常摘要', 'text', 'f_abnormal') + formField('复查建议', 'text', 'f_followup') + '</div>' +
      '<div class="form-group"><label><input type="checkbox" id="f_needfollow"> 需要复查</label></div>' +
      '<div class="modal-actions"><button class="pill-btn" onclick="closeModal()">取消</button><button class="pill-btn dark" onclick="submitProcedure()">保存</button></div>');
  };

  window.submitProcedure = async function () {
    await api('/procedures', { method: 'POST', body: JSON.stringify({
      patient_id: currentPatientId || 1, procedure_name: v('f_name'), procedure_type: v('f_type'),
      body_part: v('f_part'), facility: v('f_facility'), procedure_date: v('f_date'),
      findings: v('f_findings'), conclusion: v('f_conclusion'), abnormal_summary: v('f_abnormal'),
      follow_up_recommendation: v('f_followup'), needs_follow_up: document.getElementById('f_needfollow').checked,
    }) });
    closeModal(); renderTab('procedures');
  };

  // ── Common delete ──
  window.deleteItem = async function (type, id) {
    if (!confirm('确定删除这条记录？')) return;
    await api('/' + type + '/' + id, { method: 'DELETE' });
    closeModal();
    var active = document.querySelector('.health-tab.active');
    renderTab(active ? active.dataset.tab : 'overview');
  };

  // ── Timeline ──
  async function renderTimeline() {
    var el = $('healthContent');
    if (!currentPatientId) { el.innerHTML = '<div class="health-empty">请先选择患者</div>'; return; }
    var res = await api('/timeline?patient_id=' + currentPatientId + '&limit=200');
    var events = res.items || [];
    var typeMap = {
      encounter: { icon: '🏥', color: '#2563eb', label: '就诊' }, procedure: { icon: '🔬', color: '#7c3aed', label: '检查' },
      lab: { icon: '🧪', color: '#0891b2', label: '化验' }, medication: { icon: '💊', color: '#16a34a', label: '用药' },
      condition: { icon: '⚠️', color: '#dc2626', label: '健康问题' }, document: { icon: '📄', color: '#65a30d', label: '文档' },
    };
    var html = '<div class="page-header"><h2>健康时间线</h2><p>按时间顺序展示所有健康事件</p></div>';
    if (events.length === 0) { html += '<div class="health-empty">暂无时间线数据</div>'; }
    else {
      html += '<div class="timeline">';
      var lastDate = '';
      events.forEach(function (ev) {
        var t = typeMap[ev.event_type] || { icon: '📌', color: '#6b7280', label: ev.event_type };
        var date = fmtDate(ev.date);
        if (date !== lastDate) { html += '<div class="timeline-date">' + date + '</div>'; lastDate = date; }
        html += '<div class="timeline-item" style="border-left-color:' + t.color + '">' +
          '<div class="timeline-icon" style="background:' + t.color + '">' + t.icon + '</div>' +
          '<div class="timeline-content"><div class="timeline-title">' + esc(t.label) + ' · ' + esc(ev.title) + '</div>' +
          (ev.description ? '<div class="timeline-desc">' + esc(ev.description) + '</div>' : '') +
          (ev.status ? '<span class="badge badge-inactive">' + esc(ev.status) + '</span>' : '') +
          (ev.severity === 'warning' ? '<span class="badge badge-high">需关注</span>' : '') +
          '</div></div>';
      });
      html += '</div>';
    }
    el.innerHTML = html;
  }

  // ── Documents ──
  async function renderDocuments() {
    var el = $('healthContent');
    if (!currentPatientId) { el.innerHTML = '<div class="health-empty">请先选择患者</div>'; return; }
    var res = await api('/documents?patient_id=' + currentPatientId + '&limit=100');
    var docs = res.items || [];
    var typeLabels = { report: '检查/化验报告', prescription: '处方', medical_record: '病历', certificate: '诊断证明', bill: '费用单据', imaging: '影像资料', other: '其他' };
    var html = '<div class="page-header"><h2>文档资料</h2><p>存储看病资料原件，支持搜索和分类</p><button class="pill-btn dark" onclick="showDocumentForm()">+ 添加文档</button></div>';
    if (docs.length === 0) { html += '<div class="health-empty">暂无文档，点击上方按钮添加</div>'; }
    else {
      html += '<div class="card-grid is-minimal">';
      docs.forEach(function (d) {
        html += '<div class="video-card is-minimal health-entity-card" onclick="showDocumentDetail(' + d.id + ')">' +
          '<div class="video-card-title">📄 ' + esc(d.title) + '</div>' +
          '<div class="video-card-meta">' + esc(typeLabels[d.document_type] || d.document_type) + ' · ' + fmtDate(d.document_date) + '</div>' +
          (d.hospital ? '<div class="video-card-meta">🏥 ' + esc(d.hospital) + '</div>' : '') +
          (d.summary ? '<div class="health-card-desc">' + esc(d.summary.slice(0, 80)) + (d.summary.length > 80 ? '...' : '') + '</div>' : '') +
          (d.tags && d.tags.length ? '<div class="video-card-meta">' + d.tags.map(function (t) { return '<span class="badge badge-inactive">' + esc(t) + '</span>'; }).join(' ') + '</div>' : '') +
          '</div>';
      });
      html += '</div>';
    }
    el.innerHTML = html;
  }

  window.showDocumentForm = function () {
    modal('添加文档', '<form onsubmit="event.preventDefault();saveDocument(this)">' +
      formField('标题 *', 'text', 'doc-title') + formSelect('doc_type', [['report', '检查/化验报告'], ['prescription', '处方'], ['medical_record', '病历'], ['certificate', '诊断证明'], ['bill', '费用单据'], ['imaging', '影像资料'], ['other', '其他']]) +
      '<div class="form-row">' + formField('文档日期', 'date', 'doc-date') + formField('医院', 'text', 'doc-hospital') + '</div>' +
      formField('文件名/路径', 'text', 'doc-file', '如：CT报告.jpg') + formArea('内容摘要', 'doc-summary') + formField('标签（逗号分隔）', 'text', 'doc-tags', '如：CT,腹部,结石') +
      '<div class="modal-actions"><button class="pill-btn" onclick="closeModal()">取消</button><button class="pill-btn dark" type="submit">保存</button></div></form>');
  };

  window.saveDocument = async function (form) {
    var tags = v('doc-tags') ? v('doc-tags').split(',').map(function (s) { return s.trim(); }).filter(Boolean) : [];
    await api('/documents', { method: 'POST', body: JSON.stringify({
      patient_id: currentPatientId, title: v('doc-title'), document_type: v('doc_type'),
      document_date: v('doc-date') || null, hospital: v('doc-hospital'), file_name: v('doc-file'),
      summary: v('doc-summary'), tags: tags,
    }) });
    closeModal(); renderTab('documents');
  };

  window.showDocumentDetail = async function (id) {
    var res = await api('/documents/' + id);
    var d = res.data;
    if (!d) return;
    var typeLabels = { report: '检查/化验报告', prescription: '处方', medical_record: '病历', certificate: '诊断证明', bill: '费用单据', imaging: '影像资料', other: '其他' };
    var body = '<div class="detail">' +
      '<p><strong>类型：</strong>' + esc(typeLabels[d.document_type] || d.document_type) + '</p>' +
      '<p><strong>日期：</strong>' + fmtDate(d.document_date) + '</p>' +
      '<p><strong>医院：</strong>' + esc(d.hospital || '-') + '</p>' +
      '<p><strong>文件：</strong>' + esc(d.file_name || '-') + '</p>' +
      (d.summary ? '<p><strong>摘要：</strong><br><div class="health-pre">' + esc(d.summary) + '</div></p>' : '') +
      (d.tags && d.tags.length ? '<p><strong>标签：</strong>' + d.tags.map(function (t) { return '<span class="badge badge-inactive">' + esc(t) + '</span>'; }).join(' ') + '</p>' : '') +
      '<div class="modal-actions"><button class="pill-btn health-danger" onclick="deleteRecord(\'documents\',' + d.id + ')">删除</button></div></div>';
    modal(esc(d.title), body);
  };

  // ── Doctors ──
  async function renderDoctors() {
    var el = $('healthContent');
    var res = await api('/doctors');
    var doctors = res.items || [];
    var html = '<div class="page-header"><h2>医生信息</h2><p>记录就诊过的医生和医疗机构</p><button class="pill-btn dark" onclick="showDoctorForm()">+ 添加医生</button></div>';
    if (doctors.length === 0) { html += '<div class="health-empty">暂无医生信息，点击上方按钮添加</div>'; }
    else {
      html += '<div class="card-grid is-minimal">';
      doctors.forEach(function (d) {
        html += '<div class="video-card is-minimal health-entity-card" onclick="showDoctorDetail(' + d.id + ')">' +
          '<div class="video-card-title">👨‍⚕️ ' + esc(d.name) + (d.title ? ' · ' + esc(d.title) : '') + '</div>' +
          '<div class="video-card-meta">' + esc(d.specialty || '') + (d.department ? ' · ' + esc(d.department) : '') + '</div>' +
          (d.hospital ? '<div class="video-card-meta">🏥 ' + esc(d.hospital) + '</div>' : '') +
          (d.phone ? '<div class="video-card-meta">📞 ' + esc(d.phone) + '</div>' : '') +
          (d.notes ? '<div class="health-card-desc">' + esc(d.notes.slice(0, 60)) + (d.notes.length > 60 ? '...' : '') + '</div>' : '') +
          '</div>';
      });
      html += '</div>';
    }
    el.innerHTML = html;
  }

  window.showDoctorForm = function () {
    modal('添加医生', '<form onsubmit="event.preventDefault();saveDoctor(this)">' +
      formField('姓名 *', 'text', 'doc-name') + formField('职称', 'text', 'doc-title', '如：主任医师') +
      '<div class="form-row">' + formField('专科', 'text', 'doc-specialty', '如：肝胆胰外科') + formField('医院', 'text', 'doc-hospital') + '</div>' +
      '<div class="form-row">' + formField('科室', 'text', 'doc-dept') + formField('电话', 'text', 'doc-phone') + '</div>' +
      formField('地址', 'text', 'doc-address') + formArea('备注', 'doc-notes', '就诊体验、擅长领域等') +
      '<div class="modal-actions"><button class="pill-btn" onclick="closeModal()">取消</button><button class="pill-btn dark" type="submit">保存</button></div></form>');
  };

  window.saveDoctor = async function () {
    await api('/doctors', { method: 'POST', body: JSON.stringify({
      name: v('doc-name'), title: v('doc-title'), specialty: v('doc-specialty'),
      hospital: v('doc-hospital'), department: v('doc-dept'), phone: v('doc-phone'),
      address: v('doc-address'), notes: v('doc-notes'),
    }) });
    closeModal(); renderTab('doctors');
  };

  window.showDoctorDetail = async function (id) {
    var res = await api('/doctors/' + id);
    var d = res.data;
    if (!d) return;
    modal(esc(d.name),
      rows([['职称', d.title], ['专科', d.specialty], ['医院', d.hospital], ['科室', d.department], ['电话', d.phone], ['地址', d.address], ['备注', d.notes]]) +
      '<div class="modal-actions"><button class="pill-btn health-danger" onclick="deleteRecord(\'doctors\',' + d.id + ')">删除</button></div>');
  };


  // ── Appointments ──
  async function renderAppointments() {
    var el = $('healthContent');
    var res = await api('/appointments?upcoming_only=false&limit=200');
    var items = res.items || [];
    var upcoming = items.filter(function (a) { return a.status === 'scheduled' || a.status === 'confirmed'; });
    var past = items.filter(function (a) { return !(a.status === 'scheduled' || a.status === 'confirmed'); });

    var html = '<div class="section-header"><h2>预约 / 复诊管理</h2><button class="pill-btn dark" onclick="showAppointmentForm()">+ 新增预约</button></div>';

    if (upcoming.length > 0) {
      html += '<h3 class="health-success-text">📅 即将到来</h3><div class="card-grid is-minimal">';
      upcoming.forEach(function (a) {
        html += '<div class="video-card is-minimal health-entity-card">' +
          '<div class="health-card-head"><strong>' + esc(a.title) + '</strong><span class="badge ' + (a.status === 'confirmed' ? 'badge-active' : 'badge-inactive') + '">' + esc(a.status) + '</span></div>' +
          '<div class="video-card-meta">📆 ' + esc(a.scheduled_date) + ' ' + esc(a.scheduled_time || '') + '</div>' +
          '<div class="video-card-meta">🏥 ' + esc(a.hospital || '-') + ' · ' + esc(a.department || '-') + '</div>' +
          '<div class="video-card-meta">👨‍⚕️ ' + esc(a.doctor_name || '-') + '</div>' +
          (a.reason ? '<div class="video-card-meta">💡 ' + esc(a.reason) + '</div>' : '') +
          (a.notes ? '<div class="health-card-desc">' + esc(a.notes) + '</div>' : '') +
          '<div class="modal-actions" style="padding:0;margin-top:8px">' +
          '<button class="pill-btn health-sm" onclick="updateAppointmentStatus(' + a.id + ',\'completed\')">✓ 已完成</button>' +
          '<button class="pill-btn health-sm health-danger" onclick="deleteAppointment(' + a.id + ')">删除</button></div>' +
          '</div>';
      });
      html += '</div>';
    }

    if (past.length > 0) {
      html += '<h3 class="health-muted-text">📋 历史记录</h3><div class="card-grid is-minimal">';
      past.forEach(function (a) {
        html += '<div class="video-card is-minimal health-entity-card health-is-past">' +
          '<div class="health-card-head"><strong>' + esc(a.title) + '</strong><span class="badge badge-inactive">' + esc(a.status) + '</span></div>' +
          '<div class="video-card-meta">📆 ' + esc(a.scheduled_date) + ' ' + esc(a.scheduled_time || '') + '</div>' +
          '<div class="video-card-meta">🏥 ' + esc(a.hospital || '-') + ' · ' + esc(a.department || '-') + '</div>' +
          '</div>';
      });
      html += '</div>';
    }

    if (items.length === 0) { html += '<div class="health-empty">暂无预约记录，点击"新增预约"添加</div>'; }
    el.innerHTML = html;
  }

  window.showAppointmentForm = function () {
    var today = new Date().toISOString().slice(0, 10);
    modal('新增预约', formField('标题 *', 'text', 'ap-title', '如：胆总管结石复诊') +
      '<div class="form-row">' + formField('日期 *', 'date', 'ap-date', today) + formField('时间', 'time', 'ap-time') + '</div>' +
      '<div class="form-row"><div class="form-group"><label>类型</label><select id="ap-type"><option value="follow_up">复诊</option><option value="consultation">咨询</option><option value="checkup">体检</option><option value="procedure">检查/治疗</option><option value="other">其他</option></select></div>' +
      formField('科室', 'text', 'ap-dept', '如：肝胆胰外科') + '</div>' +
      formField('医院', 'text', 'ap-hospital', '如：宝安区中心医院') + formField('医生', 'text', 'ap-doctor', '医生姓名') +
      formArea('就诊原因', 'ap-reason') + formArea('备注', 'ap-notes') +
      '<div class="modal-actions"><button class="pill-btn dark" style="width:100%" onclick="submitAppointment()">保存预约</button></div>');
  };

  window.submitAppointment = async function () {
    var data = {
      patient_id: currentPatientId || 1, title: v('ap-title'), scheduled_date: v('ap-date'),
      appointment_type: v('ap-type'), hospital: v('ap-hospital'), department: v('ap-dept'),
      doctor_name: v('ap-doctor'), reason: v('ap-reason'), notes: v('ap-notes'),
    };
    if (!data.title || !data.scheduled_date) { alert('请填写标题和日期'); return; }
    var res = await api('/appointments', { method: 'POST', body: JSON.stringify(data) });
    if (res.ok) { closeModal(); renderAppointments(); }
    else alert('保存失败：' + (res.error || ''));
  };

  window.updateAppointmentStatus = async function (id, status) {
    await api('/appointments/' + id, { method: 'PUT', body: JSON.stringify({ status: status }) });
    renderAppointments();
  };

  window.deleteAppointment = async function (id) {
    if (!confirm('确定删除此预约？')) return;
    await api('/appointments/' + id, { method: 'DELETE' });
    renderAppointments();
  };

  window.deleteRecord = async function (type, id) {
    if (!confirm('确定删除此记录？')) return;
    await api('/' + type + '/' + id, { method: 'DELETE' });
    closeModal();
    var active = document.querySelector('.health-tab.active');
    renderTab(active ? active.dataset.tab : 'overview');
  };

  // ── Medication Logs ──
  async function renderMedicationLogs() {
    var el = $('healthContent');
    var [logsRes, medsRes, adherenceRes] = await Promise.all([
      api('/medication-logs?limit=200'), api('/medications'),
      currentPatientId ? api('/medication-adherence?patient_id=' + currentPatientId + '&days=30') : Promise.resolve({ ok: false }),
    ]);
    var logs = logsRes.items || [];
    var meds = medsRes.items || [];

    var html = '<div class="section-header"><h2>服药记录 / 用药依从性</h2><button class="pill-btn dark" onclick="showMedLogForm()">+ 记录服药</button></div>';

    if (adherenceRes.ok && adherenceRes.total_doses > 0) {
      html += '<div class="stats-grid" style="margin-bottom:20px">' +
        '<div class="stat-card"><div class="stat-value">' + (adherenceRes.adherence_rate != null ? adherenceRes.adherence_rate : 0) + '%</div><div class="stat-label">30天依从率</div></div>' +
        '<div class="stat-card"><div class="stat-value">' + (adherenceRes.taken || 0) + '</div><div class="stat-label">已服用</div></div>' +
        '<div class="stat-card"><div class="stat-value danger">' + (adherenceRes.missed || 0) + '</div><div class="stat-label">漏服</div></div>' +
        '<div class="stat-card"><div class="stat-value">' + (adherenceRes.total_doses || 0) + '</div><div class="stat-label">总记录</div></div>' +
        '</div>';
    }

    if (logs.length > 0) {
      html += '<div class="card-grid is-minimal">';
      logs.forEach(function (l) {
        var statusColor = l.status === 'taken' ? 'badge-active' : l.status === 'missed' ? 'badge-high' : 'badge-inactive';
        html += '<div class="video-card is-minimal health-entity-card">' +
          '<div class="health-card-head"><strong>' + esc(l.medication_name) + '</strong><span class="badge ' + statusColor + '">' + esc(l.status) + '</span></div>' +
          '<div class="video-card-meta">📆 ' + esc(l.scheduled_date) + ' ' + esc(l.scheduled_time || '') + '</div>' +
          (l.dosage ? '<div class="video-card-meta">💊 ' + esc(l.dosage) + '</div>' : '') +
          (l.taken_at ? '<div class="video-card-meta">✅ 实际服用：' + esc(l.taken_at) + '</div>' : '') +
          (l.notes ? '<div class="health-card-desc">' + esc(l.notes) + '</div>' : '') +
          '<div class="modal-actions" style="padding:0;margin-top:8px"><button class="pill-btn health-sm health-danger" onclick="deleteMedLog(' + l.id + ')">删除</button></div>' +
          '</div>';
      });
      html += '</div>';
    } else { html += '<div class="health-empty">暂无服药记录，点击"记录服药"添加</div>'; }

    if (meds.length > 0) {
      var activeMeds = meds.filter(function (m) { return m.status === 'active'; });
      if (activeMeds.length > 1) {
        html += '<h3 class="health-section-title">⚠️ 药物相互作用检查</h3>' +
          '<div class="health-card"><p>当前在用药物：' + activeMeds.map(function (m) { return esc(m.name); }).join('、') + '</p>' +
          '<button class="pill-btn dark" onclick="checkInteractions()">检查相互作用</button>' +
          '<div id="interaction-result" style="margin-top:12px"></div></div>';
      }
    }
    el.innerHTML = html;
  }

  window.showMedLogForm = function () {
    var today = new Date().toISOString().slice(0, 10);
    var now = new Date().toTimeString().slice(0, 5);
    modal('记录服药', formField('药物名称 *', 'text', 'ml-name', '如：匹维溴铵片') + formField('剂量', 'text', 'ml-dosage', '如：50mg 1片') +
      '<div class="form-row">' + formField('日期 *', 'date', 'ml-date', today) + formField('时间', 'time', 'ml-time', now) + '</div>' +
      '<div class="form-group"><label>状态</label><select id="ml-status"><option value="taken">已服用</option><option value="missed">漏服</option><option value="skipped">跳过</option><option value="late">延迟服用</option></select></div>' +
      formArea('备注', 'ml-notes') +
      '<div class="modal-actions"><button class="pill-btn dark" style="width:100%" onclick="submitMedLog()">保存记录</button></div>');
  };

  window.submitMedLog = async function () {
    var data = {
      patient_id: currentPatientId || 1, medication_name: v('ml-name'), dosage: v('ml-dosage'),
      scheduled_date: v('ml-date'), scheduled_time: v('ml-time'), status: v('ml-status'), notes: v('ml-notes'),
    };
    if (!data.medication_name || !data.scheduled_date) { alert('请填写药物名称和日期'); return; }
    var res = await api('/medication-logs', { method: 'POST', body: JSON.stringify(data) });
    if (res.ok) { closeModal(); renderMedicationLogs(); }
    else alert('保存失败：' + (res.error || ''));
  };

  window.deleteMedLog = async function (id) {
    if (!confirm('确定删除此记录？')) return;
    await api('/medication-logs/' + id, { method: 'DELETE' });
    renderMedicationLogs();
  };

  window.checkInteractions = async function () {
    var medsRes = await api('/medications');
    var activeMeds = (medsRes.items || []).filter(function (m) { return m.status === 'active'; });
    var result = $('interaction-result');
    if (!result) return;
    if (activeMeds.length < 2) { result.innerHTML = '<p>需要至少2种在用药物</p>'; return; }
    result.innerHTML = '<p>检查中...</p>';
    var allInteractions = [];
    for (var i = 0; i < activeMeds.length; i++) {
      var others = activeMeds.filter(function (_, j) { return j !== i; }).map(function (m) { return m.name; });
      var res = await api('/check-drug-interactions?drug_name=' + encodeURIComponent(activeMeds[i].name) + '&existing_drugs=' + encodeURIComponent(others.join(',')));
      if (res.interactions) allInteractions = allInteractions.concat(res.interactions);
    }
    var seen = new Set();
    var unique = allInteractions.filter(function (i) {
      var key = [i.drug_a, i.drug_b].sort().join('|');
      if (seen.has(key)) return false;
      seen.add(key); return true;
    });
    if (unique.length === 0) {
      result.innerHTML = '<p class="health-success-text">✅ 未发现已知药物相互作用</p>';
    } else {
      result.innerHTML = unique.map(function (i) {
        return '<div class="interaction-box ' + (i.severity === 'major' ? 'major' : '') + '">' +
          '<strong>' + esc(i.drug_a) + ' + ' + esc(i.drug_b) + '</strong>' +
          '<span class="badge ' + (i.severity === 'major' ? 'badge-high' : 'badge-inactive') + '">' + esc(i.severity) + '</span>' +
          '<p>' + esc(i.description) + '</p></div>';
      }).join('');
    }
  };

  // ── form/modal 辅助 ──
  function v(id) { var el = document.getElementById(id); return el ? el.value : ''; }
  function formField(label, type, id, ph) {
    var dt = type === 'date' || type === 'time' ? '' : (ph ? ' placeholder="' + esc(ph) + '"' : '');
    var val = '';
    if (type === 'date' || type === 'time') { if (ph) { val = ' value="' + esc(ph) + '"'; } dt = ''; }
    return '<div class="form-group"><label>' + esc(label) + '</label><input type="' + type + '" id="' + id + '"' + dt + val + '></div>';
  }
  function formArea(label, id, ph) {
    return '<div class="form-group"><label>' + esc(label) + '</label><textarea id="' + id + '" rows="2"' + (ph ? ' placeholder="' + esc(ph) + '"' : '') + '></textarea></div>';
  }
  function formSelect(id, options) {
    var s = '<div class="form-group"><label>文档类型</label><select id="' + id + '">';
    options.forEach(function (o) { s += '<option value="' + o[0] + '">' + o[1] + '</option>'; });
    return s + '</select></div>';
  }
  function rows(pairs) {
    return pairs.map(function (p) {
      var val = p[1];
      if (val == null || val === '') val = '-';
      return '<div class="detail-row"><span class="key">' + esc(p[0]) + '</span><span class="val">' + val + '</span></div>';
    }).join('');
  }

  // ── 页面打开与路由 ──
  function bindEvents() {
    var tabsEl = $('healthTabs');
    if (tabsEl) {
      tabsEl.addEventListener('click', function (e) {
        var tab = e.target.closest ? e.target.closest('.health-tab') : null;
        if (!tab) return;
        tabsEl.querySelectorAll('.health-tab').forEach(function (t) { t.classList.remove('active'); });
        tab.classList.add('active');
        renderTab(tab.dataset.tab);
      });
    }
    var overlay = $('healthModalOverlay');
    if (overlay) overlay.addEventListener('click', function (e) {
      if (e.target.id === 'healthModalOverlay') closeModal();
    });
  }

  async function openHealthPage() {
    window.showMainPage("healthPage");
    if (!initialized) { bindEvents(); initialized = true; }
    try {
      var patientsRes = await api('/patients');
      if (patientsRes.items && patientsRes.items.length === 0) {
        await api('/patients', { method: 'POST', body: JSON.stringify({ full_name: '本人', relationship: 'self' }) });
      }
      if (patientsRes.items && patientsRes.items.length > 0 && !currentPatientId) currentPatientId = patientsRes.items[0].id;
    } catch (_) { /* 后端未就绪时静默 */ }
    var active = document.querySelector('.health-tab.active');
    renderTab(active ? active.dataset.tab : 'overview');
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // 注册路由（与 topics-app.js / feed-pages.js 同模式）
  if (window.DESKTOP_PAGE_ROUTES) {
    window.DESKTOP_PAGE_ROUTES["health"] = openHealthPage;
  }
  var match = (location.pathname || "/web").match(/^\/web\/([a-zA-Z0-9-]+)\/?$/);
  var page = match ? match[1] : null;
  if (page === "health") openHealthPage();
})();