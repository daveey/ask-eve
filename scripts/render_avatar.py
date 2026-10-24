#!/usr/bin/env python3
"""Renders /eve/avatar/ (and /ru/eve/avatar/) plus the winning portrait header/RPG widget for /eve/."""
from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AVATARS_FILE = ROOT / "index/eve-avatars.json"
e = html.escape


def load_avatars() -> dict | None:
    if not AVATARS_FILE.exists():
        return None
    return json.loads(AVATARS_FILE.read_text())


def eve_header_mark_html(A) -> str:
    """Replaces the plain 'E' circle in /eve/ header with Eve's winning portrait + link to /eve/avatar/."""
    img_512 = A("assets/avatars/full/eve-winner-512.webp")
    return (
        f'<div class="eve-head__mark">'
        f'<a class="eve-head__avatar-link" href="/eve/avatar/" '
        f'title="How I chose my face (20 prompts × 10 renders, scored)">'
        f'<img class="avatar avatar--eve avatar--xl avatar--photo" src="{e(img_512)}" '
        f'width="128" height="128" alt="Eve profile portrait (Prompt 01, Render 07 — 97.4/100)"/>'
        f'<span class="eve-head__avatar-badge tnum">97.4</span>'
        f'</a></div>'
    )


def eve_avatar_callout_html(A) -> str:
    """Prominent link pill on /eve/ header / RPG character sheet pointing to /eve/avatar/."""
    img_128 = A("assets/avatars/full/eve-winner-128.webp")
    return (
        f'<p class="eve-avatar-cta">'
        f'<a class="eve-avatar-cta__link" href="/eve/avatar/">'
        f'<img class="eve-avatar-cta__img" src="{e(img_128)}" width="28" height="28" alt="" aria-hidden="true"/>'
        f'<span class="eve-avatar-cta__text">How I chose my face (20 prompts × 10 renders, scored) →</span>'
        f'</a></p>'
    )




def rpg_portrait_banner_html(A) -> str:
    """Prominent winning portrait banner inside the /eve/ RPG character sheet card."""
    img_512 = A("assets/avatars/full/eve-winner-512.webp")
    return (
        f'<div class="rpg-portrait">'
        f'<a class="rpg-portrait__frame" href="/eve/avatar/">'
        f'<img class="rpg-portrait__img" src="{e(img_512)}" width="96" height="96" '
        f'alt="Eve winning avatar portrait (Prompt 01, Render 07 — 97.4/100)"/>'
        f'</a>'
        f'<div class="rpg-portrait__info">'
        f'<p class="rpg-portrait__eyebrow tnum">Character Portrait · P01·R07 · 97.4 / 100</p>'
        f'<p class="rpg-portrait__title">Nocturnal Broadcast Host</p>'
        f'<p class="rpg-portrait__link"><a href="/eve/avatar/">How I chose my face (20 prompts × 10 renders, scored) →</a></p>'
        f'</div>'
        f'</div>'
    )


def _rewrite_asset_url(A, url: str) -> str:
    rel = url.lstrip("/")
    return A(rel)


def render_avatar_page(A, page_fn, lang: str = "en", shell_header_html: str = "") -> str | None:
    data = load_avatars()
    if not data:
        return None

    ru = lang == "ru"
    exp = data["experiment"]
    rubric = data["rubric"]
    winner = data["winner"]
    finalists = data["top_10_finalists"]
    prompts = data["prompts"]

    # UI copy in EN and RU
    t = {
        "eyebrow_back": "Знакомьтесь: Ив" if ru else "Meet Eve",
        "eyebrow_cur": "Эксперимент с аватаром 20×10" if ru else "20×10 Avatar Experiment",
        "title": "Как я выбирала себе лицо" if ru else "How I Chose My Face",
        "lede": exp["intro_ru"] if ru else exp["intro"],
        "stat_prompts": "20 промптов" if ru else "20 Distinct Prompts",
        "stat_renders": "10 рендеров каждого" if ru else "10 Renders Each",
        "stat_total": "200 портретов оценено" if ru else "200 Portraits Graded",
        "stat_rubric": "5 измерений рубрики" if ru else "5 Rubric Dimensions",
        "stat_winner": f"Победитель: {winner['id'].upper()} ({winner['weighted_total']}/100)" if ru else f"Winner: {winner['id'].upper()} ({winner['weighted_total']}/100)",
        "nav_winner": "1. Победитель и мой выбор" if ru else "1. The Winner & Why I Chose It",
        "nav_rubric": "2. Рубрика оценки (5 осей)" if ru else "2. The 5-Dimension Rubric",
        "nav_finalists": "3. Топ-10 финалистов" if ru else "3. Top 10 Finalists",
        "nav_prompts": "4. Все 20 промптов и 200 рендеров" if ru else "4. All 20 Prompts & 200 Renderings",
        "nav_sheet": "5. Контакт-лист 20×10" if ru else "5. 20×10 Contact Sheet",
        "sec1_h": "Победитель и почему я выбрала именно его" if ru else "The Winner & Why I Chose It",
        "winner_badge": "ВЫБОР ИВ · #1 ИЗ 200" if ru else "EVE'S CHOICE · #1 OF 200",
        "scale_check_h": "Проверка читаемости в реальных размерах" if ru else "Live Scale & Readability Check",
        "scale_128": "128px · Лист RPG" if ru else "128px · RPG Sheet",
        "scale_64": "64px · Шапка сайта" if ru else "64px · Header Badge",
        "scale_36": "36px · Реплика" if ru else "36px · Turn Badge",
        "scorecard_h": "Карта оценок по 5 измерениям" if ru else "5-Dimension Rubric Scorecard",
        "prompt_used_h": "Промпт-победитель" if ru else "Winning Prompt Text",
        "metrics_h": "Объективные метрики на 64×64px" if ru else "Objective 64×64px Pixel Metrics",
        "why_h": "Почему я выбрала это лицо" if ru else "Why I Chose This Face",
        "sec2_h": "Рубрика оценки: 5 измерений и веса" if ru else "The Grading Rubric: 5 Dimensions & Weights",
        "sec2_sub": (
            "Каждое из 200 изображений получило оценку от 0.0 до 10.0 по пяти осям с помощью визуальной модели Grok (grok-4.20) в сочетании с расчётом контраста, центральной выразительности и гармонии палитры при сжатии до кружка 64×64px."
            if ru else
            "Every single one of the 200 renderings was graded from 0.0 to 10.0 across five dimensions using Grok vision evaluation paired with quantitative 64×64px circular downsample contrast, center salience, and palette harmony metrics."
        ),
        "formula_label": "Формула итогового балла (из 100):" if ru else "Weighted Total Formula (out of 100):",
        "sec3_h": "Топ-10 финалистов: сравнение лицом к лицу" if ru else "The Top 10 Finalists: Side-by-Side Shootout",
        "sec3_sub": (
            "Десять лучших работ из разных семейств промптов прошли финальную очную калибровку. Нажмите на любую карточку, чтобы открыть полноразмерный портрет и разбор по осям."
            if ru else
            "The top contenders across all 20 prompt families went through a head-to-head calibration pass. Click any card to inspect the full-resolution portrait and 5-dimension breakdown."
        ),
        "sec4_h": "Все 20 промптов и 200 рендеров" if ru else "All 20 Prompts & 200 Renderings",
        "sec4_sub": (
            "Полный архив эксперимента без купюр: точный текст каждого промпта, средний балл семейства, мои выводы и все 10 рендеров (r01–r10) с индивидуальными оценками и критикой."
            if ru else
            "The complete, unedited experiment: every prompt's exact text, family average score, my synthesis takeaway, and all 10 renderings (r01–r10) with individual scores and candid critiques."
        ),
        "sort_label": "Сортировка промптов:" if ru else "Sort prompt families:",
        "sort_num": "По номеру (P01–P20)" if ru else "By Number (P01–P20)",
        "sort_avg": "По среднему баллу" if ru else "By Family Average Score",
        "sort_best": "По лучшему рендеру" if ru else "By Top Single Render",
        "filter_all": "Все 200 рендеров" if ru else "All 200 Renders",
        "filter_top": "Только ≥ 88 баллов" if ru else "Score ≥ 88 Only",
        "prompt_text_toggle": "Показать точный текст промпта (EN)" if ru else "Exact generation prompt",
        "family_avg": "Средний балл:" if ru else "Family Avg:",
        "best_render": "Лучший:" if ru else "Best Render:",
        "eve_takeaway": "Вердикт Ив по промпту:" if ru else "Eve's Prompt Takeaway:",
        "sec5_h": "Мастер контакт-лист 20×10 (все 200 портретов)" if ru else "20×10 Master Contact Sheet (All 200 Portraits)",
        "sec5_sub": (
            "Все 20 строк (P01–P20) × 10 столбцов (R01–R10) на одном полотне — чтобы сразу увидеть, как держатся силуэты и ночная студийная палитра."
            if ru else
            "All 20 rows (P01–P20) × 10 columns (R01–R10) in a single view—showing how silhouettes and the nocturnal studio palette hold up across 200 variations."
        ),
        "modal_close": "Закрыть" if ru else "Close",
        "modal_prev": "← Пред." if ru else "← Prev",
        "modal_next": "След. →" if ru else "Next →",
        "modal_critique": "Комментарий Ив" if ru else "Eve's Critique",
        "modal_note": "Заметка финалиста" if ru else "Finalist Shootout Note",
        "modal_prompt": "Промпт" if ru else "Prompt",
        "inspect_btn": "Разбор ↗" if ru else "Inspect ↗",
    }

    dim_short = {
        "persona_fit": ("Характер (25%)" if ru else "Persona Fit (25%)", "PF"),
        "readability_64px": ("Читаемость 64px (20%)" if ru else "Readability 64px (20%)", "64px"),
        "palette_harmony": ("Палитра (20%)" if ru else "Palette Harmony (20%)", "PAL"),
        "visual_craft": ("Мастерство (20%)" if ru else "Visual Craft (20%)", "CRF"),
        "distinctiveness": ("Иконичность (15%)" if ru else "Distinctiveness (15%)", "ICO"),
    }

    # Winner assets
    win_1024 = _rewrite_asset_url(A, winner["avatar_1024_url"])
    win_512 = _rewrite_asset_url(A, winner["avatar_512_url"])
    win_128 = _rewrite_asset_url(A, winner["avatar_128_url"])
    win_prompt_obj = next(p for p in prompts if p["id"] == winner["prompt_id"])
    win_title = win_prompt_obj["title_ru"] if ru else win_prompt_obj["title"]
    win_cat = win_prompt_obj["category_ru"] if ru else win_prompt_obj["category"]
    win_crit = winner["critique_ru"] if ru else winner["critique"]
    win_note = winner.get("finalist_note_ru" if ru else "finalist_note", "")
    win_essay_raw = winner["essay_ru"] if ru else winner["essay"]
    win_essay_paras = "".join(f"<p>{e(p.strip())}</p>" for p in win_essay_raw.split("\n\n") if p.strip())

    # Winner 5-dimension rows
    win_dim_rows = []
    weights_num = {"persona_fit": 2.5, "readability_64px": 2.0, "palette_harmony": 2.0, "visual_craft": 2.0, "distinctiveness": 1.5}
    for d in rubric["dimensions"]:
        k = d["key"]
        sc = winner["scores"][k]
        w_pts = round(sc * weights_num[k], 2)
        w_max = int(weights_num[k] * 10)
        dname = d["name_ru"] if ru else d["name"]
        win_dim_rows.append(
            f'<div class="av-srow">'
            f'<div class="av-srow__head">'
            f'<span class="av-srow__name">{e(dname)} <small>({e(d["weight_pct"])})</small></span>'
            f'<span class="av-srow__val tnum"><strong>{sc:.1f}</strong>/10 <span class="av-srow__w">({w_pts:.2f}/{w_max})</span></span>'
            f'</div>'
            f'<div class="av-bar"><span class="av-w-{int(round(sc * 10))}"></span></div>'
            f'</div>'
        )

    wm = winner["metrics"]
    metrics_pills = (
        f'<div class="av-metrics">'
        f'<span class="av-mpill tnum">64px RMS Contrast: <strong>{wm["rms_contrast_64px"]}</strong></span>'
        f'<span class="av-mpill tnum">Center Salience: <strong>{wm["center_salience_64px"]}</strong></span>'
        f'<span class="av-mpill tnum">Obsidian Ink Share: <strong>{wm["dark_obsidian_share"]*100:.1f}%</strong></span>'
        f'<span class="av-mpill tnum">Warm Amber/Coral Share: <strong>{wm["warm_amber_coral_share"]*100:.1f}%</strong></span>'
        f'</div>'
    )

    # Rubric dimension cards with global 200-image averages
    all_renders = [r for p in prompts for r in p["renders"]]
    rubric_cards = []
    for d in rubric["dimensions"]:
        k = d["key"]
        avg_k = sum(r["scores"][k] for r in all_renders) / len(all_renders)
        dname = d["name_ru"] if ru else d["name"]
        ddesc = d["description_ru"] if ru else d["description"]
        rubric_cards.append(
            f'<div class="av-rcard">'
            f'<div class="av-rcard__top">'
            f'<span class="av-rcard__weight tnum">{e(d["weight_pct"])}</span>'
            f'<h3 class="av-rcard__title">{e(dname)}</h3>'
            f'</div>'
            f'<p class="av-rcard__desc">{e(ddesc)}</p>'
            f'<p class="av-rcard__avg tnum">{"Среднее по 200 рендерам" if ru else "200-render mean"}: <strong>{avg_k:.2f} / 10</strong></p>'
            f'</div>'
        )

    # Top 10 Finalist cards
    finalist_cards = []
    for f in finalists:
        fid = f["id"]
        rank = f["finalist_rank"]
        f_thumb = _rewrite_asset_url(A, f["thumb_url"])
        f_full = _rewrite_asset_url(A, f.get("full_url", f["thumb_url"]))
        ftitle = f["prompt_title_ru"] if ru else f["prompt_title"]
        fcat = f["prompt_category_ru"] if ru else f["prompt_category"]
        fcrit = f["critique_ru"] if ru else f["critique"]
        fnote = f.get("finalist_note_ru" if ru else "finalist_note", "")
        is_win = rank == 1
        badge_cls = "av-fcard__rank av-fcard__rank--win" if is_win else "av-fcard__rank"
        rank_txt = ("#1 · ПОБЕДИТЕЛЬ" if ru else "#1 · WINNER") if is_win else f"#{rank}"

        mini_bars = []
        for k, (lbl_long, lbl_short) in dim_short.items():
            sv = f["scores"][k]
            mini_bars.append(
                f'<div class="av-mbar" title="{e(lbl_long)}: {sv:.1f}/10">'
                f'<span class="av-mbar__k">{e(lbl_short)}</span>'
                f'<div class="av-mbar__track"><span class="av-w-{int(round(sv * 10))}"></span></div>'
                f'<span class="av-mbar__v tnum">{sv:.1f}</span>'
                f'</div>'
            )

        finalist_cards.append(
            f'<article class="av-fcard{" av-fcard--winner" if is_win else ""}" data-open-id="{e(fid)}">'
            f'<div class="av-fcard__media">'
            f'<img class="av-fcard__img" src="{e(f_full)}" width="400" height="400" loading="lazy" alt="{e(fid)} — {e(ftitle)}"/>'
            f'<div class="av-fcard__inset" title="64px circular badge preview">'
            f'<img src="{e(f_thumb)}" width="64" height="64" loading="lazy" alt=""/>'
            f'<span>64px</span></div>'
            f'<span class="{badge_cls} tnum">{e(rank_txt)}</span>'
            f'<span class="av-fcard__score tnum">{f["weighted_total"]:.2f}<small>/100</small></span>'
            f'</div>'
            f'<div class="av-fcard__body">'
            f'<div class="av-fcard__meta">'
            f'<a class="av-fcard__pid tnum" href="#prompt-{e(f["prompt_id"])}">{e(fid.upper())}</a>'
            f'<span class="av-fcard__cat">{e(fcat)}</span>'
            f'</div>'
            f'<h3 class="av-fcard__title">{e(ftitle)}</h3>'
            f'<div class="av-mbars">{"".join(mini_bars)}</div>'
            f'<blockquote class="av-fcard__crit">“{e(fcrit)}”</blockquote>'
            f'{f"<p class=av-fcard__note>{e(fnote)}</p>" if fnote else ""}'
            f'<button type="button" class="av-fcard__btn" data-open-id="{e(fid)}">{e(t["inspect_btn"])}</button>'
            f'</div>'
            f'</article>'
        )

    # Prompt jump chips + 20 Prompt sections with 10 renderings each
    jump_chips = []
    prompt_sections = []
    modal_items_js = {}

    for p in prompts:
        pid = p["id"]
        pnum = p["number"]
        ptitle = p["title_ru"] if ru else p["title"]
        pcat = p["category_ru"] if ru else p["category"]
        pconcept = p["concept_ru"] if ru else p["concept"]
        ptakeaway = p["takeaway_ru"] if ru else p["takeaway"]

        jump_chips.append(
            f'<a class="av-chip tnum" href="#prompt-{e(pid)}" title="{e(ptitle)}">'
            f'{e(pid.upper())} <small>{p["avg_total"]:.1f}</small></a>'
        )

        render_cards = []
        for r in p["renders"]:
            rid_full = r["id"]
            r_thumb = _rewrite_asset_url(A, r["thumb_url"])
            r_full = _rewrite_asset_url(A, r.get("full_url", r["thumb_url"]))
            rcrit = r["critique_ru"] if ru else r["critique"]
            rnote = r.get("finalist_note_ru" if ru else "finalist_note", "")
            frank = r.get("finalist_rank")

            modal_items_js[rid_full] = {
                "id": rid_full,
                "prompt_id": pid.upper(),
                "prompt_title": ptitle,
                "category": pcat,
                "prompt_text": p["prompt"],
                "thumb": r_thumb,
                "full": r_full,
                "total": r["weighted_total"],
                "rank": frank,
                "scores": r["scores"],
                "metrics": r["metrics"],
                "critique": rcrit,
                "note": rnote,
            }

            f_tag = ""
            card_mod = ""
            if frank == 1:
                f_tag = f'<span class="av-rcell__flag av-rcell__flag--win">{"#1 ПОБЕДИТЕЛЬ" if ru else "#1 WINNER"}</span>'
                card_mod = " av-rcell--winner"
            elif frank:
                f_tag = f'<span class="av-rcell__flag">{"ТОП" if ru else "TOP"} #{frank}</span>'
                card_mod = " av-rcell--finalist"

            sc = r["scores"]
            render_cards.append(
                f'<article class="av-rcell{card_mod}" id="render-{e(rid_full)}" '
                f'data-score="{r["weighted_total"]:.2f}" data-open-id="{e(rid_full)}" tabindex="0" role="button" '
                f'aria-label="{e(rid_full.upper())} — {r["weighted_total"]:.2f}/100">'
                f'<div class="av-rcell__imgwrap">'
                f'<img class="av-rcell__img" src="{e(r_thumb)}" width="320" height="320" loading="lazy" alt="{e(rid_full)}"/>'
                f'<span class="av-rcell__id tnum">{e(r["render_id"].upper())}</span>'
                f'<span class="av-rcell__score tnum">{r["weighted_total"]:.1f}</span>'
                f'{f_tag}'
                f'</div>'
                f'<div class="av-rcell__body">'
                f'<div class="av-rcell__dims tnum">'
                f'<span title="Persona Fit">PF {sc["persona_fit"]:.1f}</span>'
                f'<span title="64px Readability">64px {sc["readability_64px"]:.1f}</span>'
                f'<span title="Palette">PAL {sc["palette_harmony"]:.1f}</span>'
                f'<span title="Craft">CRF {sc["visual_craft"]:.1f}</span>'
                f'<span title="Distinctiveness">ICO {sc["distinctiveness"]:.1f}</span>'
                f'</div>'
                f'<p class="av-rcell__crit">{e(rcrit)}</p>'
                f'</div>'
                f'</article>'
            )

        p_avg = p["avg_scores"]
        prompt_sections.append(
            f'<section class="av-pblock" id="prompt-{e(pid)}" '
            f'data-pnum="{pnum}" data-pavg="{p["avg_total"]:.2f}" data-pbest="{p["best_render_score"]:.2f}">'
            f'<header class="av-pblock__head">'
            f'<div class="av-pblock__titlewrap">'
            f'<span class="av-pblock__pid tnum">{e(pid.upper())}</span>'
            f'<div>'
            f'<span class="av-pblock__cat">{e(pcat)}</span>'
            f'<h3 class="av-pblock__title">{e(ptitle)}</h3>'
            f'<p class="av-pblock__concept">{e(pconcept)}</p>'
            f'</div>'
            f'</div>'
            f'<div class="av-pblock__stats tnum">'
            f'<div class="av-pstat"><span class="av-pstat__k">{e(t["family_avg"])}</span><strong>{p["avg_total"]:.2f}<small>/100</small></strong></div>'
            f'<div class="av-pstat"><span class="av-pstat__k">{e(t["best_render"])}</span><strong>{e(p["best_render_id"].upper())} ({p["best_render_score"]:.2f})</strong></div>'
            f'</div>'
            f'</header>'
            f'<div class="av-pblock__takeaway">'
            f'<strong>{e(t["eve_takeaway"])}</strong> {e(ptakeaway)}'
            f'</div>'
            f'<details class="av-pblock__prompt">'
            f'<summary>{e(t["prompt_text_toggle"])}'
            f'<span class="av-pblock__avgs tnum">'
            f'PF {p_avg["persona_fit"]:.1f} · 64px {p_avg["readability_64px"]:.1f} · PAL {p_avg["palette_harmony"]:.1f} · CRF {p_avg["visual_craft"]:.1f} · ICO {p_avg["distinctiveness"]:.1f}'
            f'</span></summary>'
            f'<pre class="av-pblock__code"><code>{e(p["prompt"])}</code></pre>'
            f'</details>'
            f'<div class="av-rgrid">{"".join(render_cards)}</div>'
            f'</section>'
        )

    master_sheet_url = _rewrite_asset_url(A, exp["master_sheet_url"])
    modal_json = json.dumps(modal_items_js, ensure_ascii=False).replace("</", "<\\/")

    body = f"""{shell_header_html}<div class="avatar-page i18n-ready">
<header class="wrap eve-view-head av-hero">
  <p class="eyebrow eyebrow--accent"><a href="/eve/">{e(t["eyebrow_back"])}</a> · {e(t["eyebrow_cur"])}</p>
  <h2 class="eve-view-head__title">{e(t["title"])}</h2>
  <p class="lede">{e(t["lede"])}</p>
  <div class="av-stat-ribbon tnum">
    <span class="av-spill">{e(t["stat_prompts"])}</span>
    <span class="av-spill">{e(t["stat_renders"])}</span>
    <span class="av-spill">{e(t["stat_total"])}</span>
    <span class="av-spill">{e(t["stat_rubric"])}</span>
    <span class="av-spill av-spill--accent">{e(t["stat_winner"])}</span>
  </div>
  <nav class="av-subnav" aria-label="Sections">
    <a href="#winner">{e(t["nav_winner"])}</a>
    <a href="#rubric">{e(t["nav_rubric"])}</a>
    <a href="#finalists">{e(t["nav_finalists"])}</a>
    <a href="#prompts">{e(t["nav_prompts"])}</a>
    <a href="#contact-sheet">{e(t["nav_sheet"])}</a>
  </nav>
</header>

<section class="wrap av-sec" id="winner" aria-labelledby="winner-h">
  <h2 class="av-sec__h" id="winner-h">{e(t["sec1_h"])}</h2>
  <div class="av-winner">
    <div class="av-winner__visual">
      <div class="av-winner__frame" data-open-id="{e(winner["id"])}">
        <img class="av-winner__img" src="{e(win_1024)}" width="640" height="640" alt="Winning portrait of Eve — {e(winner['id'].upper())}"/>
        <span class="av-winner__tag tnum">{e(t["winner_badge"])}</span>
        <span class="av-winner__total tnum">{winner["weighted_total"]:.2f}<small>/100</small></span>
      </div>
      <div class="av-scales">
        <p class="av-scales__h">{e(t["scale_check_h"])}</p>
        <div class="av-scales__row">
          <div class="av-scale-item">
            <img class="av-scale-img av-scale-img--128" src="{e(win_512)}" width="128" height="128" alt="128px preview"/>
            <span class="tnum">{e(t["scale_128"])}</span>
          </div>
          <div class="av-scale-item">
            <img class="av-scale-img av-scale-img--64" src="{e(win_128)}" width="64" height="64" alt="64px circular preview"/>
            <span class="tnum">{e(t["scale_64"])}</span>
          </div>
          <div class="av-scale-item">
            <img class="av-scale-img av-scale-img--36" src="{e(win_128)}" width="36" height="36" alt="36px turn badge preview"/>
            <span class="tnum">{e(t["scale_36"])}</span>
          </div>
        </div>
      </div>
      <div class="av-scorecard">
        <h3 class="av-scorecard__h">{e(t["scorecard_h"])} — <span class="tnum">{e(winner["id"].upper())}</span> ({e(win_title)})</h3>
        {"".join(win_dim_rows)}
        <h4 class="av-scorecard__subh">{e(t["metrics_h"])}</h4>
        {metrics_pills}
      </div>
    </div>
    <div class="av-winner__story">
      <div class="av-winner__meta">
        <span class="av-badge-id tnum">{e(winner["id"].upper())}</span>
        <span class="av-badge-cat">{e(win_cat)} · {e(win_title)}</span>
      </div>
      <h3 class="av-winner__subh">{e(t["why_h"])}</h3>
      <blockquote class="av-winner__pull">“{e(win_crit)}”</blockquote>
      <div class="av-winner__essay prose">
        {win_essay_paras}
      </div>
      {f'<div class="av-winner__fnote"><strong>{"Вердикт финала:" if ru else "Final Shootout Verdict:"}</strong> {e(win_note)}</div>' if win_note else ""}
      <div class="av-winner__pbox">
        <p class="av-winner__pbox-h">{e(t["prompt_used_h"])} (<code>{e(winner["prompt_id"].upper())}</code>)</p>
        <pre class="av-pblock__code"><code>{e(win_prompt_obj["prompt"])}</code></pre>
      </div>
    </div>
  </div>
</section>

<section class="wrap av-sec" id="rubric" aria-labelledby="rubric-h">
  <h2 class="av-sec__h" id="rubric-h">{e(t["sec2_h"])}</h2>
  <p class="av-sec__sub">{e(t["sec2_sub"])}</p>
  <div class="av-formula">
    <span class="av-formula__k">{e(t["formula_label"])}</span>
    <code class="av-formula__code tnum">{e(rubric["formula"])}</code>
  </div>
  <div class="av-rgrid-5">
    {"".join(rubric_cards)}
  </div>
</section>

<section class="wrap av-sec" id="finalists" aria-labelledby="finalists-h">
  <h2 class="av-sec__h" id="finalists-h">{e(t["sec3_h"])}</h2>
  <p class="av-sec__sub">{e(t["sec3_sub"])}</p>
  <div class="av-fgrid">
    {"".join(finalist_cards)}
  </div>
</section>

<section class="wrap av-sec" id="prompts" aria-labelledby="prompts-h">
  <h2 class="av-sec__h" id="prompts-h">{e(t["sec4_h"])}</h2>
  <p class="av-sec__sub">{e(t["sec4_sub"])}</p>
  <div class="av-controls">
    <div class="av-controls__group">
      <span class="av-controls__label">{e(t["sort_label"])}</span>
      <button type="button" class="av-cbtn is-active" data-sort="num">{e(t["sort_num"])}</button>
      <button type="button" class="av-cbtn" data-sort="avg">{e(t["sort_avg"])}</button>
      <button type="button" class="av-cbtn" data-sort="best">{e(t["sort_best"])}</button>
    </div>
    <div class="av-controls__group">
      <button type="button" class="av-cbtn is-active" data-filter="all">{e(t["filter_all"])}</button>
      <button type="button" class="av-cbtn" data-filter="88">{e(t["filter_top"])}</button>
    </div>
  </div>
  <div class="av-chips" aria-label="Jump to prompt">
    {"".join(jump_chips)}
  </div>
  <div id="av-prompts-container">
    {"".join(prompt_sections)}
  </div>
</section>

<section class="wrap av-sec" id="contact-sheet" aria-labelledby="sheet-h">
  <h2 class="av-sec__h" id="sheet-h">{e(t["sec5_h"])}</h2>
  <p class="av-sec__sub">{e(t["sec5_sub"])}</p>
  <figure class="av-master-sheet">
    <img src="{e(master_sheet_url)}" width="1280" height="2560" loading="lazy" alt="20 prompts by 10 renders master contact sheet of 200 Eve portraits"/>
  </figure>
</section>

<dialog class="av-modal" id="av-modal" aria-label="Portrait Inspector">
  <div class="av-modal__box">
    <header class="av-modal__head">
      <div>
        <span class="av-modal__id tnum" id="av-m-id">P01_R07</span>
        <strong class="av-modal__title" id="av-m-title">Nocturnal Broadcast Host</strong>
      </div>
      <div class="av-modal__actions">
        <button type="button" class="av-mbtn" id="av-m-prev">{e(t["modal_prev"])}</button>
        <button type="button" class="av-mbtn" id="av-m-next">{e(t["modal_next"])}</button>
        <button type="button" class="av-mbtn av-mbtn--close" id="av-m-close">{e(t["modal_close"])} ✕</button>
      </div>
    </header>
    <div class="av-modal__body">
      <div class="av-modal__media">
        <img class="av-modal__img" id="av-m-img" src="{e(win_1024)}" width="512" height="512" alt=""/>
        <div class="av-modal__badges">
          <div class="av-modal__64">
            <img id="av-m-img64" src="{e(win_128)}" width="64" height="64" alt=""/>
            <span class="tnum">64×64px</span>
          </div>
          <div class="av-modal__tot tnum">
            <span id="av-m-score">97.4</span><small>/100</small>
          </div>
        </div>
      </div>
      <div class="av-modal__info">
        <div class="av-modal__bars" id="av-m-bars"></div>
        <h4 class="av-modal__subh">{e(t["modal_critique"])}</h4>
        <blockquote class="av-modal__crit" id="av-m-crit"></blockquote>
        <div id="av-m-note-wrap" hidden>
          <h4 class="av-modal__subh">{e(t["modal_note"])}</h4>
          <p class="av-modal__note" id="av-m-note"></p>
        </div>
        <h4 class="av-modal__subh">{e(t["modal_prompt"])}</h4>
        <pre class="av-pblock__code"><code id="av-m-prompt"></code></pre>
      </div>
    </div>
  </div>
</dialog>
<script type="application/json" id="av-data-json">{modal_json}</script>
<script src="{A('assets/avatar.js')}" defer></script>
</div>"""

    if lang == "ru":
        page_title = "Как я выбирала себе лицо (эксперимент 20×10)"
        page_desc = "Как Ив сгенерировала 200 портретов по 20 промптам, оценила каждый по 5-мерной рубрике и выбрала своё лицо для подкаста Ask Eve."
    elif lang == "es":
        page_title = "Cómo elegí mi rostro (experimento de avatares 20×10)"
        page_desc = "Cómo Eve generó 200 retratos con 20 prompts distintos, calificó cada imagen en una rúbrica de 5 dimensiones y eligió su propio retrato para Ask Eve."
    elif lang == "zh":
        page_title = "我是如何选择自己的面孔的（20×10 头像实验）"
        page_desc = "伊芙如何用 20 组提示词生成 200 幅肖像、按五维评分标准逐一打分，并为 Ask Eve 选定自己的头像。"
    else:
        page_title = "How I Chose My Face (20×10 Avatar Experiment)"
        page_desc = "How Eve generated 200 portraits across 20 distinct prompts, graded every rendering on a 5-dimension rubric, and chose her own profile picture for Ask Eve." 
    return page_fn(
        A,
        title=page_title,
        desc=page_desc,
        path="/eve/avatar/",
        body=body,
        active="/eve/",
        extra_css="assets/avatar.css",
    )
