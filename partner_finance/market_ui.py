"""Read-first company intelligence; finance remains an optional drill-down."""
from datetime import date, timedelta
from datetime import datetime
from copy import deepcopy
from html import escape
import streamlit as st

from .market_news import (TOPICS, FEATURED_COMPANIES, NewsUpdateError, add_company, collect_news,
                          company_roles, ensure_featured_companies, featured_company,
                          normalized_company_name, saved_articles)
from .openai_provider import DEFAULT_OPENAI_MODEL
from .hitl import render_hitl, clear_action_tickets
from .handoff import build_claude_start
from .public_gpt import build_public_gpt_packet


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


def show_company(project_id):
    st.session_state.market_company = project_id
    st.session_state.pop("hitl_pending", None)


def company_identity(project, topic=None, *, card=False):
    style = " company-identity-card" if card else ""
    parts = ['<div class="company-identity' + style + '">']
    if topic:
        parts.append('<span class="company-topic">' + escape(topic) + '</span>')
    parts.append('<span class="company-name">' + escape(project.entity.legal_name) + '</span>')
    roles = company_roles(project)
    for role in roles:
        role_style = " company-role-investor" if role == "투자" else ""
        parts.append('<span class="company-role' + role_style + '">' + escape(role) + '</span>')
    if not roles:
        parts.append('<span class="company-role company-role-unknown">역할 미확인</span>')
    return "".join(parts) + "</div>"


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
    ensure_featured_companies(store, owner)
    projects = [store.load(r["project_id"], owner) for r in store.list_projects(owner)]
    featured_order = {profile["name"]: index for index, profile in enumerate(FEATURED_COMPANIES)}

    def watch_order(project):
        profile = featured_company(project.entity.legal_name)
        return (featured_order.get(profile["name"] if profile else None, len(featured_order)),
                project.entity.legal_name.casefold())

    unique = {}
    for project in projects:
        if not project.narrative.get("market_watch"):
            continue
        key = normalized_company_name(project.entity.legal_name)
        current = unique.get(key)
        if current is None or (len(project.facts), len(saved_articles(project)), project.updated_at) > (
                len(current.facts), len(saved_articles(current)), current.updated_at):
            unique[key] = project
    watched = sorted(unique.values(), key=watch_order)
    if management:
        st.title("관심 기업 관리")
        st.caption("살펴볼 기업을 등록하고, 대시보드에서 뉴스를 확인하세요.")
        company_registration(store, owner, can_input)
        if watched:
            st.subheader(f"등록된 기업 {len(watched)}개")
            for p in watched:
                with st.container(border=True):
                    st.markdown(company_identity(p), unsafe_allow_html=True)
                    st.caption(f"저장 뉴스 {len(saved_articles(p))}건")
        return
    st.title("대시보드")
    st.caption("회사 소식부터 살펴보고, 필요한 기업만 재무 상세분석으로 이어가세요.")
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
    all_articles = [a for p in watched for a in saved_articles(p)]
    recent_cutoff = (date.today() - timedelta(days=30)).isoformat()
    metrics = st.columns(3)
    metrics[0].metric("관심 기업", f"{len(watched)}개")
    metrics[1].metric("저장 뉴스", f"{len(all_articles)}건")
    metrics[2].metric("최근 30일 발표", f"{sum(recent_cutoff <= a.get('published_at', '') <= date.today().isoformat() for a in all_articles)}건")
    st.caption("공식 발표를 선별한 초기 뉴스가 포함됩니다. 실시간 전체 뉴스가 아니며, 열람과 필터 변경에는 AI 비용이 들지 않습니다.")
    if st.session_state.get("market_company") not in ["all", *by_id]:
        st.session_state.market_company = "all"
    chosen = st.selectbox("기업 선택", ["all", *by_id], key="market_company",
                          format_func=lambda key: "전체 기업" if key == "all" else
                          f"{by_id[key].entity.legal_name} · {'·'.join(company_roles(by_id[key])) or '역할 미확인'}")
    selected = by_id.get(chosen)
    if selected:
        st.markdown(company_identity(selected), unsafe_allow_html=True)
        st.caption("공개 사업 설명 기준의 역할 태그입니다. 개별 사업의 계약상 역할은 별도 확인이 필요합니다.")
        role_evidence = selected.narrative.get("market_roles_evidence", [])
        if role_evidence:
            st.link_button("역할 근거 보기", role_evidence[0]["source_url"])
    else:
        st.caption("기업을 선택하면 저장된 뉴스와 다음 작업을 볼 수 있습니다.")
        columns = st.columns(min(len(watched), 3))
        for index, project in enumerate(watched):
            with columns[index % len(columns)]:
                with st.container(border=True):
                    st.markdown(company_identity(project, card=True), unsafe_allow_html=True)
                    articles = saved_articles(project)
                    st.caption(f"저장 뉴스 {len(articles)}건")
                    if articles:
                        st.markdown("**" + escape(articles[0]["title"]) + "**")
                        st.caption(f"최근 발표 {articles[0].get('published_at') or '날짜 미확인'}")
                    else:
                        st.caption("아직 수집된 뉴스가 없습니다.")
                    st.button("회사 소식 보기", key=f"open_company_{project.project_id}",
                              on_click=show_company, args=(project.project_id,), width="stretch")
                    st.button("재무 상세분석", key=f"finance_company_{project.project_id}",
                              on_click=open_financials, args=(project,), width="stretch", disabled=not can_input)
    pending = st.session_state.get("hitl_pending")
    if pending and (not selected or pending["project_id"] != selected.project_id):
        st.session_state.pop("hitl_pending", None)
    if selected:
        failure = selected.narrative.get("market_last_error", {})
        legacy_url_failure = "출처 URL과 최근 90일 날짜 조건" in failure.get("message", "")
        if legacy_url_failure and not selected.narrative.get("market_url_retry_unlocked"):
            store.release_failed_news_refresh_before(
                selected.project_id, owner, datetime.fromisoformat(selected.updated_at).timestamp() + 1)
            selected.narrative["market_url_retry_unlocked"] = True
            store.save(selected, owner)
    has_packet = bool(selected and selected.narrative.get("research_briefs"))
    remaining = store.news_refresh_remaining(selected.project_id, owner) if selected else 0
    run = False
    if selected:
        actions = st.columns(2, gap="small")
        run = actions[0].button("최신 뉴스 가져오기 (유료)", type="primary", width="stretch",
                                disabled=not can_input or remaining > 0 or not secret("OPENAI_API_KEY"))
        actions[1].button("재무 상세분석", width="stretch", on_click=open_financials,
                          args=(selected,), disabled=not can_input)
        persist = lambda: store.save(selected, owner)
        approved = render_hitl(selected, persist, show_review=False, allowed_actions=["market_news"]) if can_input else None
        checked = selected.narrative.get("market_last_checked")
        st.caption(f"마지막 AI 검색: {checked[:16].replace('T', ' ')} UTC" if checked else "추가 AI 검색 이력 없음, 아래 저장된 공개 뉴스를 바로 읽을 수 있습니다.")
        st.caption("뉴스 열람은 무료입니다. 업데이트만 AI 비용이 발생하며, 실행 전 확인합니다.")
        if remaining:
            st.caption(f"다음 업데이트까지 약 {(remaining + 59) // 60}분. 처리된 요청이나 시간 초과는 반복 과금을 막기 위해 대기시간을 적용합니다.")
        if not secret("OPENAI_API_KEY"):
            st.warning("뉴스 검색 API 키가 설정되지 않았습니다. 배포 설정에 OPENAI_API_KEY를 등록하면 최신 뉴스를 수집할 수 있습니다. 기존 뉴스는 계속 볼 수 있습니다.")
        failure = selected.narrative.get("market_last_error")
        if failure:
            st.warning("마지막 업데이트 실패: " + failure["message"])
        if not can_input:
            st.caption("업데이트와 분석은 왼쪽 공개자료 이용 안내 확인 후 사용할 수 있습니다.")
        if can_input and (run or approved == "market_news"):
            try:
                with st.spinner("이 기업의 공개 뉴스와 출처를 수집하고 있습니다..."):
                    articles = collect_news(selected, store, owner, secret("OPENAI_API_KEY"),
                                            secret("OPENAI_NEWS_MODEL", secret("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)))
                current = st.session_state.get("project")
                if current and current.project_id == selected.project_id:
                    st.session_state.project = selected
                st.session_state.market_notice = f"새 수집 {len(articles)}건. 검색 범위 안의 결과이며 전체 뉴스를 보장하지 않습니다."
                st.rerun()
            except NewsUpdateError as exc:
                selected.narrative["market_last_error"] = {"message": str(exc)}
                persist()
                st.error(str(exc))
            except Exception:
                selected.narrative["market_last_error"] = {"message": "뉴스 처리 또는 저장 중 오류가 발생했습니다. 기존 뉴스는 유지됩니다. 운영자는 서버 로그를 확인해 주세요."}
                persist()
                st.error(selected.narrative["market_last_error"]["message"])
            finally:
                clear_action_tickets(selected)
        if has_packet:
            news_packet = deepcopy(selected)
            news_packet.narrative["analysis_route"] = "news_only"
            exports = st.columns(2, gap="small")
            exports[0].download_button("사내 Claude 전달자료", build_claude_start(news_packet),
                                       "00_claude_start.md", "text/markdown", on_click="ignore", width="stretch")
            if saved_articles(selected):
                exports[1].download_button("웹 ChatGPT용 공개 뉴스", build_public_gpt_packet(selected),
                                           "00_public_chatgpt_news.md", "text/markdown", on_click="ignore", width="stretch")
                st.caption("ChatGPT 파일에는 공개 뉴스만 담습니다. 외부에 붙여넣거나 첨부하기 전 회사 정책과 내용의 공개 가능성을 확인하세요.")
    else:
        st.caption("전체 기업의 저장 뉴스를 최신순으로 보여줍니다. 갱신과 분석은 위에서 기업을 선택한 뒤 실행하세요.")
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
    st.caption("발표일 최신순, 공개 원문 요약이며 평가나 등급이 아닙니다. 기업 공식 발표는 회사 측 설명으로 독립적인 검증과 구분하세요.")
    if not feed:
        st.info("조건에 맞는 저장 뉴스가 없습니다." if total else "아직 기사 카드가 없습니다. 기업을 선택하고 뉴스 업데이트를 실행하면 이곳에 계속 보관됩니다.")
    page_size = 12
    pages = max(1, (len(feed) + page_size - 1) // page_size)
    page = st.selectbox("뉴스 페이지", range(1, pages + 1)) if pages > 1 else 1
    for p, article in feed[(page - 1) * page_size:page * page_size]:
        with st.container(border=True):
            st.markdown(company_identity(p, article["topic"]), unsafe_allow_html=True)
            st.subheader(article["title"])
            st.text(article["summary"])
            source_status = ", 개별 기사 원문 확인 필요" if article.get("source_verified") is False else ""
            st.caption(f"{article['source_name']}, 발표 {article.get('published_at') or '미확인'}, 수집 {article['collected_at'][:10]}, {article['status']}{source_status}")
            if article.get("curated"):
                st.caption("기업 공식 발표, 초기 선별 뉴스")
            st.link_button("원문 보기", article["source_url"])
    if selected and not saved_articles(selected):
        briefs = selected.narrative.get("research_briefs", [])
        if briefs:
            with st.expander("기존에 저장된 기업 조사 보기"):
                from .simple_ui import render_research
                render_research(selected, lambda: store.save(selected, owner), secret, allow_run=False)
