"""Static presentation adapter for the private personal profile."""

from __future__ import annotations

import html
from typing import Any


SEX_LABELS = {
    "male": "男性",
    "female": "女性",
    "intersex": "间性",
    "unspecified": "未填写",
}
STATUS_LABELS = {
    "active": "当前",
    "monitoring": "观察中",
    "resolved": "已缓解",
    "historical": "既往",
    "current": "当前",
}
CATEGORY_LABELS = {
    "examination": "检查",
    "diagnosis": "诊断",
    "laboratory": "检验",
    "imaging": "影像",
    "treatment": "治疗",
    "prescription": "处方",
    "vaccination": "疫苗",
    "other": "其他",
}
PROVENANCE_LABELS = {
    "migrated_local_config": "由旧版本地档案迁移",
    "user_reported": "用户提供",
    "medical_record": "病历记录",
    "mixed": "用户陈述与病历记录",
}
REMINDER_STATUS_LABELS = {
    "active": "已启用",
    "configured-not-loaded": "配置存在但未加载",
    "stale-workspace": "仓库位置已变化，请重新设置",
    "orphaned-agent": "发现孤立任务，请移除后重设",
    "disabled": "未启用",
}
DIET_CONTEXT_LABELS = {
    "food_photo_means_consumed": "食物照片约定",
    "photo_logging_convention": "拍照记录规则",
    "usual_breakfast": "常见早餐",
    "usual_lunch_dinner": "常见午晚餐",
    "usual_snacks": "常见加餐",
    "oily_fish_frequency": "油性鱼频次",
    "milk_whey_trial": "牛奶与乳清观察",
}
CONTEXT_LABELS = {
    "digestive": "消化系统",
    "skin": "皮肤",
    "sleep": "睡眠",
    "caffeine": "咖啡因",
}


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _display(value: Any, suffix: str = "") -> str:
    if value in (None, "", []):
        return "未填写"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        return "、".join(_escape(item) for item in value) or "未填写"
    return _escape(value) + suffix


def _list(items: Any, fallback: str = "未记录") -> str:
    if not isinstance(items, list) or not items:
        return f'<p class="empty">{_escape(fallback)}</p>'
    return "<ul>" + "".join(f"<li>{_escape(item)}</li>" for item in items) + "</ul>"


def _health_cards(rows: Any, *, name_key: str, empty: str) -> str:
    if not isinstance(rows, list) or not rows:
        return f'<p class="empty">{_escape(empty)}</p>'
    cards: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = _escape(row.get(name_key, "未命名"))
        status = STATUS_LABELS.get(str(row.get("status")), str(row.get("status", "")))
        frequency = row.get("frequency")
        secondary = f" · {_escape(frequency)}" if frequency else ""
        references = row.get("record_ids", [])
        reference_note = (
            f'<p class="record-ref">关联病历 {_escape(len(references))} 条</p>'
            if isinstance(references, list) and references
            else ""
        )
        cards.append(
            '<article class="status-card">'
            f'<div><span class="pill">{_escape(status)}</span>{secondary}</div>'
            f"<h3>{name}</h3>{_list(row.get('notes'), '暂无补充说明')}"
            f"{reference_note}</article>"
        )
    return "".join(cards) or f'<p class="empty">{_escape(empty)}</p>'


def _medical_timeline(records: Any) -> str:
    if not isinstance(records, list) or not records:
        return '<p class="empty">尚未建立病历索引。原件可放入本档案的 medical/files/，并在 index.json 中登记。</p>'
    entries: list[str] = []
    ordered = sorted(
        (row for row in records if isinstance(row, dict)),
        key=lambda row: str(row.get("date", "")),
        reverse=True,
    )
    for record in ordered:
        category = CATEGORY_LABELS.get(
            str(record.get("category")), str(record.get("category", "其他"))
        )
        status = STATUS_LABELS.get(
            str(record.get("status")), str(record.get("status", ""))
        )
        sources = record.get("source_files", [])
        source_count = len(sources) if isinstance(sources, list) else 0
        provider = record.get("provider")
        provider_line = f" · {_escape(provider)}" if provider else ""
        entries.append(
            '<article class="timeline-card">'
            f'<div class="timeline-meta"><time>{_display(record.get("date"))}</time>'
            f"<span>{_escape(category)} · {_escape(status)}{provider_line}</span></div>"
            f"<h3>{_escape(record.get('title', '未命名病历'))}</h3>"
            "<h4>摘要</h4>"
            f"{_list(record.get('summary'), '暂无摘要')}"
            "<h4>所见或结论</h4>"
            f"{_list(record.get('findings'), '暂无结构化所见')}"
            f'<p class="source-note">原件 {source_count} 份，仅保存在私有 data 层；网页不提供文件链接。</p>'
            "</article>"
        )
    return "".join(entries)


def _target_rows(targets: dict[str, Any]) -> str:
    labels = (
        ("protein_g", "全天蛋白质", "g"),
        ("fat_g", "脂肪", "g"),
        ("fiber_g", "膳食纤维", "g"),
        ("sodium_mg_max", "钠上限", "mg"),
    )
    rows: list[str] = []
    for key, label, unit in labels:
        value = targets.get(key)
        if isinstance(value, list) and len(value) == 2:
            rendered = f"{_escape(value[0])}–{_escape(value[1])} {unit}"
        elif value is not None:
            rendered = f"{_escape(value)} {unit}"
        else:
            rendered = "未填写"
        rows.append(f"<tr><th>{_escape(label)}</th><td>{rendered}</td></tr>")
    tracking = targets.get("tracking", {})
    if isinstance(tracking, dict):
        for key, label, unit in (
            ("protein_per_meal_g", "每餐蛋白质", "g"),
            ("direct_water_ml_base", "基础直接饮水", "mL"),
            ("calcium_mg_target", "钙", "mg"),
            ("sleep_hours_min", "睡眠下限", "h"),
        ):
            value = tracking.get(key)
            if isinstance(value, list) and len(value) == 2:
                rendered = f"{_escape(value[0])}–{_escape(value[1])} {unit}"
            elif value is not None:
                rendered = f"{_escape(value)} {unit}"
            else:
                rendered = "未填写"
            rows.append(f"<tr><th>{_escape(label)}</th><td>{rendered}</td></tr>")
    return "".join(rows)


def render_personal_profile_html(
    personal_profile: dict[str, Any],
    medical_index: dict[str, Any],
    *,
    generated_at: str,
    profile_source: str,
    medical_index_source: str,
    warnings: list[str] | None = None,
    reminder: dict[str, Any] | None = None,
) -> str:
    """Render a self-contained private profile without raw-record links."""

    demographics = personal_profile.get("demographics", {})
    current = personal_profile.get("current_status", {})
    activity = personal_profile.get("activity", {})
    targets = personal_profile.get("nutrition_targets", {})
    diet = personal_profile.get("diet_context", {})
    health = personal_profile.get("health_status", {})
    provenance = personal_profile.get("provenance", {})
    context_notes = health.get("context_notes", {}) if isinstance(health, dict) else {}
    conditions = health.get("conditions", []) if isinstance(health, dict) else []
    symptoms = health.get("symptoms", []) if isinstance(health, dict) else []
    medications = health.get("medications", []) if isinstance(health, dict) else []
    allergies = health.get("allergies", []) if isinstance(health, dict) else []
    records = medical_index.get("records", [])
    sex = SEX_LABELS.get(
        str(demographics.get("sex", "unspecified")),
        str(demographics.get("sex", "unspecified")),
    )
    diet_rows = (
        "".join(
            f"<tr><th>{_escape(DIET_CONTEXT_LABELS.get(key, key))}</th><td>{_display(value)}</td></tr>"
            for key, value in diet.items()
        )
        or '<tr><td colspan="2" class="empty">尚未填写饮食背景</td></tr>'
    )
    context_rows = "".join(
        f'<article class="note"><h3>{_escape(CONTEXT_LABELS.get(key, key))}</h3><p>{_display(context_notes.get(key))}</p></article>'
        for key in ("digestive", "skin", "sleep", "caffeine")
    )
    warning_block = (
        '<aside class="warning"><strong>档案提醒</strong>'
        + _list(warnings, "")
        + "</aside>"
        if warnings
        else ""
    )
    goals = current.get("goals", [])
    goal_chips = (
        "".join(f'<span class="goal">{_escape(goal)}</span>' for goal in goals)
        if isinstance(goals, list) and goals
        else '<span class="goal muted-goal">未填写目标</span>'
    )
    provenance_source = PROVENANCE_LABELS.get(
        str(provenance.get("source", "")), str(provenance.get("source", ""))
    )
    reminder = reminder or {"status": "disabled"}
    reminder_status = REMINDER_STATUS_LABELS.get(
        str(reminder.get("status", "disabled")),
        str(reminder.get("status", "disabled")),
    )
    reminder_time = reminder.get("time") or "未设置"
    reminder_open = "是" if reminder.get("open_dashboard") else "否"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>个人档案 · Local HealthLog</title>
  <style>
    :root{{--ink:#18231c;--muted:#69746d;--paper:#f2f0e9;--panel:#fffdf8;--line:#dce2da;--green:#185b43;--soft:#e7f0e9;--gold:#e0b65d;--warn:#fff4d8}}
    *{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
    main{{width:min(1120px,calc(100% - 30px));margin:auto;padding:48px 0 72px}} h1,h2,h3,h4,p{{margin-top:0}} h1{{font-size:clamp(42px,7vw,76px);line-height:1;letter-spacing:-.05em;margin-bottom:16px}} h2{{font-size:27px;letter-spacing:-.025em}} h3{{font-size:17px;margin-bottom:7px}} h4{{margin:18px 0 4px;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em}}
    .eyebrow{{color:var(--green);font-size:12px;font-weight:850;letter-spacing:.13em;text-transform:uppercase}} .muted,.empty{{color:var(--muted)}}
    .hero{{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(270px,.7fr);gap:28px;padding:clamp(28px,5vw,54px);border:1px solid var(--line);border-radius:30px;background:linear-gradient(135deg,#fffdf8,#e9f1e9);box-shadow:0 18px 50px rgba(32,55,43,.07)}}
    .hero-side{{padding:20px;border-radius:20px;background:#174d3a;color:#f0f6f2}} .hero-side p{{color:#bed3c7}} .hero-side strong{{display:block;font-size:19px}} .goals{{display:flex;flex-wrap:wrap;gap:8px;margin-top:22px}} .goal,.pill{{display:inline-flex;padding:5px 10px;border-radius:999px;background:var(--soft);color:var(--green);font-size:12px;font-weight:750}} .muted-goal{{color:var(--muted)}}
    .facts{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:22px}} .fact{{padding:16px;border:1px solid var(--line);border-radius:16px;background:rgba(255,255,255,.72)}} .fact span{{display:block;color:var(--muted);font-size:12px}} .fact strong{{display:block;font-size:20px;margin-top:3px}}
    .section{{margin-top:24px;padding:clamp(22px,4vw,34px);border:1px solid var(--line);border-radius:24px;background:var(--panel)}} .section-head{{display:flex;justify-content:space-between;gap:20px;align-items:flex-end;margin-bottom:18px}} .section-head p{{max-width:580px;margin-bottom:0;color:var(--muted)}}
    .grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:14px}} .grid-3{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}} .status-card,.note{{padding:18px;border:1px solid var(--line);border-radius:17px;background:white}} .status-card ul,.note p{{margin-bottom:0}} .status-card .pill{{margin-bottom:10px}} .record-ref,.source-note{{color:var(--muted);font-size:12px}}
    table{{width:100%;border-collapse:collapse}} th,td{{padding:11px 9px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}} th{{width:34%;color:var(--muted);font-weight:650}} tr:last-child th,tr:last-child td{{border-bottom:0}}
    .timeline{{border-left:2px solid #bed3c5;padding-left:19px}} .timeline-card{{position:relative;margin:0 0 16px;padding:20px;border:1px solid var(--line);border-radius:18px;background:white}} .timeline-card:before{{content:"";position:absolute;left:-26px;top:25px;width:11px;height:11px;border-radius:50%;background:var(--gold);border:3px solid var(--paper)}} .timeline-meta{{display:flex;justify-content:space-between;gap:16px;color:var(--muted);font-size:12px}} .timeline-card h3{{font-size:21px;margin-top:8px}} .timeline-card ul{{margin:5px 0}}
    .warning{{margin-top:20px;padding:16px 18px;border:1px solid #ead39a;border-radius:16px;background:var(--warn)}} .warning ul{{margin-bottom:0}} .privacy{{margin-top:24px;padding:24px;border-radius:21px;background:#173f31;color:#edf5f0}} .privacy p{{color:#c4d8cc}} code{{padding:2px 5px;border-radius:6px;background:#e9f1eb;color:var(--green);overflow-wrap:anywhere}} .hero code{{white-space:nowrap}} .privacy code{{background:rgba(255,255,255,.08);color:#ddecdf}}
    footer{{margin-top:20px;color:var(--muted);font-size:12px}}
    @media(max-width:820px){{.hero,.grid-2,.grid-3{{grid-template-columns:1fr}}.facts{{grid-template-columns:1fr 1fr}}.section-head,.timeline-meta{{align-items:flex-start;flex-direction:column}}}}
    @media(max-width:470px){{.facts{{grid-template-columns:1fr}}main{{width:min(100% - 20px,1120px);padding-top:22px}}}}
  </style>
</head>
<body><main>
  <section class="hero">
    <div><p class="eyebrow">Private personal profile</p><h1>{_escape(personal_profile.get("display_name", "个人档案"))}</h1><p class="muted">这是饮食分析、健康建议和趋势解释共同读取的个人背景。修改主档案后运行 <code>diet profile</code> 即可重新校验和展示。</p><div class="goals">{goal_chips}</div>
      <div class="facts">
        <div class="fact"><span>年龄</span><strong>{_display(demographics.get("age_years"), " 岁")}</strong></div>
        <div class="fact"><span>性别</span><strong>{_escape(sex)}</strong></div>
        <div class="fact"><span>身高</span><strong>{_display(demographics.get("height_cm"), " cm")}</strong></div>
        <div class="fact"><span>基线体重</span><strong>{_display(current.get("weight_kg"), " kg")}</strong></div>
      </div>
    </div>
    <aside class="hero-side"><p class="eyebrow">Profile ownership</p><strong>{_escape(personal_profile.get("profile_id", ""))}</strong><p>最近复核：{_display(provenance.get("last_reviewed"))}</p><p>档案状态：本地私有、结构化、可校验</p><p>来源：{_display(provenance_source)}</p></aside>
  </section>
  {warning_block}

  <section class="section"><div class="section-head"><div><p class="eyebrow">Current context</p><h2>身体状况与症状</h2></div><p>状态来自用户陈述或已登记病历；它为分析提供约束，但不自动生成诊断。</p></div>
    <div class="grid-2"><div><h3>疾病或需要观察的情况</h3>{_health_cards(conditions, name_key="name", empty="未登记疾病或观察项")}</div><div><h3>症状</h3>{_health_cards(symptoms, name_key="name", empty="未登记症状")}</div></div>
    <div class="grid-2" style="margin-top:14px"><div><h3>当前药物</h3>{_health_cards(medications, name_key="name", empty="未登记当前药物")}</div><div><h3>过敏</h3>{_health_cards(allergies, name_key="substance", empty="未登记过敏")}</div></div>
  </section>

  <section class="section"><div class="section-head"><div><p class="eyebrow">Medical history</p><h2>往期病历</h2></div><p>这里展示可检索的摘要与关联关系。原始检查单、影像和 PDF 始终留在私有 data 层。</p></div><div class="timeline">{_medical_timeline(records)}</div></section>

  <section class="section"><div class="section-head"><div><p class="eyebrow">Lifestyle</p><h2>活动与日常背景</h2></div><p>这些稳定背景用于解释热量、蛋白质、恢复和长期趋势。</p></div>
    <div class="grid-3">
      <article class="note"><h3>工作模式</h3><p>{_display(activity.get("work_pattern"))}</p></article>
      <article class="note"><h3>力量训练</h3><p>{_display(activity.get("strength_sessions_per_week"))} 次/周</p></article>
      <article class="note"><h3>专项运动</h3><p>网球 {_display(activity.get("tennis_hours_per_week"))} h/周 · 游泳 {_display(activity.get("swimming_hours_per_week"))} h/周</p></article>
    </div><div class="grid-2" style="margin-top:14px">{context_rows}</div>
  </section>

  <section class="section"><div class="section-head"><div><p class="eyebrow">Nutrition context</p><h2>目标与饮食背景</h2></div><p>目标属于个人档案；运行配置只负责目录、隐私开关和 Shortcut 名称。</p></div><div class="grid-2"><table><tbody>{_target_rows(targets)}</tbody></table><table><tbody>{diet_rows}</tbody></table></div></section>

  <section class="section"><div class="section-head"><div><p class="eyebrow">Daily reminder</p><h2>每日本地提醒</h2></div><p>提醒由本机 launchd 运行；静态网页只展示状态，不直接修改系统任务。</p></div>
    <div class="facts">
      <div class="fact"><span>状态</span><strong>{_escape(reminder_status)}</strong></div>
      <div class="fact"><span>本地时间</span><strong>{_escape(reminder_time)}</strong></div>
      <div class="fact"><span>提醒时打开门户</span><strong>{reminder_open}</strong></div>
      <div class="fact"><span>设置命令</span><strong><code>diet reminder set --time HH:MM</code></strong></div>
    </div><p class="source-note" style="margin-top:14px">通知可能显示在锁屏；默认文案不包含健康详情。使用 <code>diet reminder test</code> 试发，使用 <code>diet reminder remove</code> 关闭。</p>
  </section>

  <section class="section"><div class="section-head"><div><p class="eyebrow">Safety boundaries</p><h2>补剂与解释边界</h2></div><p>这些规则优先于从单日照片推断出的普通建议。</p></div>{_list(health.get("supplement_guardrails") if isinstance(health, dict) else [], "尚未设置补剂边界")}</section>

  <section class="privacy"><h2>本地数据边界</h2><p>结构化主档案：<code>{_escape(profile_source)}</code><br>病历索引：<code>{_escape(medical_index_source)}</code></p><p>网页不会包含病历原件的路径或可点击链接。备份 data 层；runtime 与 site 可以由主档案重新生成。对健康情况作出实质修改后，请同时更新 <code>updated_at</code> 与 <code>provenance.last_reviewed</code>。</p></section>
  <footer>生成于 {_escape(generated_at)} · 个人健康档案用于记录与分析上下文，不替代医疗诊断。</footer>
</main></body></html>"""
