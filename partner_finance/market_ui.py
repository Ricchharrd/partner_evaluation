"""Read-first company intelligence; finance remains an optional drill-down."""
from datetime import date, timedelta
from copy import deepcopy
from html import escape
import streamlit as st

from .market_news import TOPICS, add_company, collect_news, saved_articles
from .openai_provider import DEFAULT_OPENAI_MODEL
from .hitl import render_hitl, clear_action_tickets


def open_financials(project):
    st.session_state.project = project
    project.narrative["analysis_route"] = "public_financials"
    st.session_state.pop(f"route_{project.project_id}", None)
    st.session_state[f"step_{project.project_id}"] = "2. 결과 확인" if project.facts else "1. 자료 준비"
    st.session_state.workspace_view = "재무 상세분석"
    st.session_state.pop("hitl_pending", None)


def show_all_companies():
    st.session_state.market_company = "all"
    st.session_state.pop("hitl_pending", None)


def company_registration(store, owner, can_input):
    st.caption("공개 기업명만 등록하세요. 기업 추가와 저장된 뉴스 열람에는 AI 비용이 들지 않습니다.")
    if not can_input:
        st.info("기업을 추가하려면 왼쪽 메뉴에서 공개자료 이용 안내를 확인해 주세요.")
    with st.form("market_add"):
        name = st.text_input("기업명", placeholder="예: Acciona, Webuild", max_chars=120, disabled=not can_input)
        add = st.form_submit_button("관심 기업에 추가", type="primary", disabled=not can_input)
    if add and can_input:
        try:
            p = add_company(store, owner, name)
            st.session_state.market_company = p.project_id
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


def render_market(store, owner, secret, *, management=False, can_input=True):
    projects = [store.load(r["project_id"], owner) for r in store.list_projects(owner)]
    watched = sorted((p for p in projects if p.narrative.get("market_watch")), key=lambda p: p.entity.legal_name.casefold())
    if management:
        st.title("관심 기업 관리")
        st.caption("살펴볼 기업을 등록하고, 대시보드에서 뉴스를 확인하세요.")
        company_registration(store, owner, can_input)
        if watched:
            st.subheader(f"등록된 기업 {len(watched)}개")
            for p in watched:
                with st.container(border=True):
                    st.write(p.entity.legal_name)
                    st.caption(f"저장 뉴스 {len(saved_articles(p))}건")
        return
    st.title("대시보드")
    st.caption("기업별 최신 소식을 확인하세요. 재무 상세분석은 필요할 때만 진행합니다.")
    if not watched:
        st.subheader("첫 관심 기업을 등록해 보세요")
        st.caption("한 번 수집한 뉴스는 다시 검색하지 않고 계속 읽을 수 있습니다.")
        company_registration(store, owner, can_input)
        cols = st.columns(2)
        for col, name in zip(cols, ("Acciona", "Webuild")):
            if col.button(f"{name} 추가", width="stretch", disabled=not can_input) and can_input:
                p = add_company(store, owner, name)
                st.session_state.market_company = p.project_id
                st.rerun()
        st.info("아직 저장된 뉴스가 없습니다. 기업 등록만으로 유료 검색이 실행되지는 않습니다.")
        return

    by_id = {p.project_id: p for p in watched}
    if st.session_state.get("market_company") not in ["all", *by_id]:
        st.session_state.market_company = "all"
    chosen = st.pills("기업 선택", ["all", *by_id], key="market_company",
                          format_func=lambda key: "전체 기업" if key == "all" else by_id[key].entity.legal_name)
    selected = by_id.get(chosen)
    st.caption("기업을 선택하면 해당 기업의 뉴스와 분석 메뉴를 볼 수 있습니다.")
    pending = st.session_state.get("hitl_pending")
    if pending and (not selected or pending["project_id"] != selected.project_id):
        st.session_state.pop("hitl_pending", None)
    has_packet = bool(selected and selected.narrative.get("research_briefs"))
    actions = st.columns([1.4, 1, 1.4, 1.4] if has_packet else [1.4, 1, 1.4], gap="small")
    actions[0].button("전체 기업 소식", on_click=show_all_companies, width="stretch")
    remaining = store.news_refresh_remaining(selected.project_id, owner) if selected else 0
    run = actions[1].button("업데이트", type="primary", width="stretch",
                            disabled=not selected or not can_input or remaining > 0 or not secret("OPENAI_API_KEY"))
    actions[2].button("재무 상세분석", width="stretch", on_click=open_financials,
                      args=(selected,), disabled=selected is None or not can_input)
    if selected:
        persist = lambda: store.save(selected, owner)
        approved = render_hitl(selected, persist, show_review=False, allowed_actions=["market_news"]) if can_input else None
        checked = selected.narrative.get("market_last_checked")
        st.caption(f"마지막 수집: {checked[:16].replace('T', ' ')} UTC" if checked else "아직 뉴스 업데이트를 실행하지 않았습니다.")
        st.caption("뉴스 열람은 무료입니다. 업데이트만 AI 비용이 발생하며, 실행 전 확인합니다.")
        if remaining:
            st.caption(f"다음 업데이트까지 약 {(remaining + 59) // 60}분. 실패한 요청도 반복 과금을 막기 위해 대기시간을 적용합니다.")
        if not secret("OPENAI_API_KEY"):
            st.caption("새 뉴스 수집 연결을 준비 중입니다. 저장된 뉴스는 계속 볼 수 있습니다.")
        if not can_input:
            st.caption("업데이트와 분석은 왼쪽 공개자료 이용 안내 확인 후 사용할 수 있습니다.")
        if can_input and (run or approved == "market_news"):
            try:
                with st.spinner("이 기업의 공개 뉴스와 출처를 수집하고 있습니다..."):
                    articles = collect_news(selected, store, owner, secret("OPENAI_API_KEY"), secret("OPENAI_MODEL", DEFAULT_OPENAI_MODEL))
                current = st.session_state.get("project")
                if current and current.project_id == selected.project_id:
                    st.session_state.project = selected
                st.session_state.market_notice = f"새 수집 {len(articles)}건. 검색 범위 안의 결과이며 전체 뉴스를 보장하지 않습니다."
                st.rerun()
            except Exception:
                st.error("뉴스 업데이트를 완료하지 못했습니다. 기존 뉴스는 유지됩니다. 연결, 사용 한도 또는 뉴스 출처를 확인해 주세요.")
            finally:
                clear_action_tickets(selected)
        if selected.narrative.get("research_briefs"):
            try:
                from .handoff import build_claude_start
                news_packet = deepcopy(selected)
                news_packet.narrative["analysis_route"] = "news_only"
                packet = build_claude_start(news_packet)
            except ImportError:
                st.error("Claude 전달자료를 생성할 수 없습니다. 배포 모듈 상태를 확인해 주세요.")
            else:
                actions[3].download_button("Claude 전달자료", packet,
                                           "00_claude_start.md", "text/markdown", on_click="ignore", width="stretch")
    else:
        st.caption("전체 기업의 저장 뉴스를 최신순으로 보여줍니다. 업데이트와 분석은 기업을 선택한 뒤 실행하세요.")
    if st.session_state.get("market_notice"):
        st.info(st.session_state.pop("market_notice"))

    controls = st.columns([2, 1, 1])
    query = controls[0].text_input("뉴스 찾기", placeholder="수주, 실적, 소송 등 저장 뉴스 안에서 검색")
    period = controls[1].selectbox("발표 기간", ["전체", "최근 7일", "최근 30일", "최근 90일"])
    topic = controls[2].selectbox("뉴스 주제", ["전체", *TOPICS])
    scope = [selected] if selected else watched
    feed = [(p, article) for p in scope for article in saved_articles(p)]
    total = len(feed)
    if topic != "전체":
        feed = [(p, a) for p, a in feed if a["topic"] == topic]
    if query.strip():
        q = query.casefold().strip()
        feed = [(p, a) for p, a in feed if q in " ".join([p.entity.legal_name, a["title"], a["summary"], a["topic"]]).casefold()]
    if period != "전체":
        cutoff = (date.today() - timedelta(days=int(period.split()[1][:-1]))).isoformat()
        feed = [(p, a) for p, a in feed if a.get("published_at", "") >= cutoff]
        st.caption("발표일이 확인되지 않은 기사는 기간 필터에서 제외됩니다.")
    feed.sort(key=lambda pair: (pair[1].get("published_at", ""), pair[1]["collected_at"]), reverse=True)
    st.subheader(f"{selected.entity.legal_name + ' 소식' if selected else '전체 기업 소식'}, {len(feed)}건")
    st.caption("최신순, AI 정리 후 검토 대기 중인 자료입니다. 기사가 없다는 것이 위험이 없다는 뜻은 아닙니다.")
    if not feed:
        st.info("조건에 맞는 저장 뉴스가 없습니다." if total else "아직 기사 카드가 없습니다. 기업을 선택하고 뉴스 업데이트를 실행하면 이곳에 계속 보관됩니다.")
    for p, article in feed[:60]:
        with st.container(border=True):
            st.markdown('<div class="company-identity"><span class="company-topic">'
                        + escape(article["topic"]) + '</span><span class="company-name">'
                        + escape(p.entity.legal_name) + '</span></div>', unsafe_allow_html=True)
            st.subheader(article["title"])
            st.text(article["summary"])
            st.caption(f"{article['source_name']}, 발표 {article.get('published_at') or '미확인'}, 수집 {article['collected_at'][:10]}, {article['status']}")
            st.link_button("원문 보기", article["source_url"])
    if len(feed) > 60:
        st.caption("최신 60건을 표시합니다. 기업과 검색어로 범위를 줄여 주세요.")
    if selected and not saved_articles(selected):
        briefs = selected.narrative.get("research_briefs", [])
        if briefs:
            with st.expander("기존에 저장된 기업 조사 보기"):
                from .simple_ui import render_research
                render_research(selected, lambda: store.save(selected, owner), secret, allow_run=False)
