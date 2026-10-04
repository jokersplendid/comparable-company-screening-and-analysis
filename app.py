from pathlib import Path
from io import BytesIO
import sys, json, hashlib, uuid, os
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import streamlit as st
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from src.data_loader import (read_workbook, quality_report, create_input_template,
                             read_raw_workbook, suggest_column_mapping,
                             build_tables_from_mapping, REQUIRED, OPTIONAL, FIELD_LABELS)
from src.rule_filter import screen_candidates
from src.scoring import (QUALITATIVE_DIMENSIONS, QUANTITATIVE_DIMENSIONS,
                         GROUP_DEFAULT_WEIGHTS, score_breakdown, quantitative_scores)
from src.llm_client import LLMClient
from src.comparison import compare_client_list, build_rule_tasks
from src.candidate_pool import build_candidate_pool
from src.workpaper_filler import append_rationales_column, append_valuation_sheets
from src.valuation import (build_peer_valuation_detail, multiple_statistics,
                           target_valuation, valuation_summary, valuation_calculation_detail,
                           apply_valuation_plan)
from src.review_state import apply_action, export_ready, INCLUDED, EXCLUDED, REVIEWED, PENDING

st.set_page_config(page_title="可比公司智能筛选助手",page_icon="🔎",layout="wide")
st.title("可比公司智能筛选与合理性分析助手")
st.caption("AI 辅助决策系统 · 最终筛选与审计判断由人工负责")

STEPS=["① 数据上传与质量检查","② 规则筛选与权重","③ AI相似度与候选排名","④ 人工确认","⑤ 估值分析与导出"]
ANALYSIS_SCHEMA_VERSION="valuation-workflow-v8"
def go_to_step(delta):
    current=STEPS.index(st.session_state.workflow_page)
    st.session_state.workflow_page=STEPS[max(0,min(len(STEPS)-1,current+delta))]

st.markdown("""
<style>
    [data-testid="stAppViewContainer"] { background:#f7f8f6; }
    [data-testid="stSidebar"] { background:#eef1f3; }
    .stepbar {display:flex; gap:0; margin:12px 0 26px; border-bottom:1px solid #d9ddd5;}
    .stepitem {flex:1; padding:12px 10px; color:#7b8178; border-bottom:3px solid transparent; font-weight:600;}
    .stepitem.active {color:#273b22; border-color:#91a83a;}
    .stepitem.done {color:#62772a;}
    div.stButton > button[kind="primary"] {background:#91a83a; border-color:#91a83a; color:white;}
    .workflow-footer {border-top:1px solid #d9ddd5; margin-top:28px; padding-top:18px;}
    div[data-testid="stHorizontalBlock"]:has(.review-header-marker) {
        background:#e7eddd; border:1px solid #cbd5b8; border-radius:8px;
        padding:10px 12px 7px; margin-top:8px;
    }
    .review-header-marker {display:none;}
    .status-badge {display:inline-block; padding:4px 10px; border-radius:999px; font-weight:650; white-space:nowrap;}
    .status-pending {background:#fff2cc; color:#8a5a00; border:1px solid #ead38b;}
    .status-reviewed {background:#dfeef8; color:#155b85; border:1px solid #b9d6e8;}
    .decision-undecided {background:#eceff2; color:#58616b; border:1px solid #d6dbe0;}
    .decision-included {background:#e1f3e5; color:#23713a; border:1px solid #b9ddc1;}
    .decision-excluded {background:#fae3e3; color:#a33a3a; border:1px solid #edbebe;}
</style>
""",unsafe_allow_html=True)

def text_value(value):
    if value is None or pd.isna(value): return ""
    value=str(value).strip()
    return "" if value.casefold() in {"nan","none"} else value

def initial_rationale(row):
    return row.get("comparability_analysis") or "当前未生成完整的可比性原因分析。请检查模型输出，或补充人工复核意见后重新生成。"

def status_badge(value,kind):
    css={
        ("review",PENDING):"status-pending",("review",REVIEWED):"status-reviewed",
        ("decision",""):"decision-undecided",("decision",INCLUDED):"decision-included",
        ("decision",EXCLUDED):"decision-excluded"}
    label=value or "未决定"
    return f'<span class="status-badge {css.get((kind,value),"decision-undecided")}">{label}</span>'

def current_llm_client():
    config=st.session_state.get("llm_config",{})
    return LLMClient(api_key=config.get("api_key") or None,base_url=config.get("base_url") or None,model=config.get("model") or None,thinking=config.get("thinking") or "disabled")

def calculate_pair_scores(ai_scores, investee, candidate, settings, growth_tolerance, margin_tolerance):
    qualitative_dims=settings.get("qualitative_dimensions",QUALITATIVE_DIMENSIONS)
    quantitative_dims=settings.get("quantitative_dimensions",QUANTITATIVE_DIMENSIONS)
    qualitative={d["name"]:ai_scores.get(d["name"]) for d in qualitative_dims}
    quantitative=quantitative_scores(investee,candidate,quantitative_dims,growth_tolerance,margin_tolerance)
    scores={**qualitative,**quantitative}
    breakdown=score_breakdown(scores,{d["name"]:d["weight"] for d in qualitative_dims},{d["name"]:d["weight"] for d in quantitative_dims},settings["groups"])
    return scores,breakdown

if "tables" not in st.session_state:
    try:
        demo_path=ROOT/"sample_data"/"demo_input.xlsx"
        st.session_state.tables=read_workbook(demo_path)
        st.session_state.source_workbook_bytes=demo_path.read_bytes()
        st.session_state.source_workbook_name=demo_path.name
    except Exception: st.session_state.tables={}
if "decisions" not in st.session_state: st.session_state.decisions={}
if st.session_state.get("analysis_schema_version")!=ANALYSIS_SCHEMA_VERSION:
    for stale_key in ("task_analysis","rule_tasks","candidate_pool","candidate_pool_summary","candidate_pool_min_size","run_log","single_test_task_id"):
        st.session_state.pop(stale_key,None)
    st.session_state["analysis_schema_version"]=ANALYSIS_SCHEMA_VERSION
if "llm_config" not in st.session_state: st.session_state.llm_config={"api_key":os.getenv("LLM_API_KEY",""),"base_url":os.getenv("LLM_BASE_URL","https://api.openai.com/v1"),"model":os.getenv("LLM_MODEL",""),"thinking":os.getenv("LLM_THINKING","disabled")}
if "qualitative_dimensions" not in st.session_state: st.session_state.qualitative_dimensions=[{**d,"id":f"q{i}"} for i,d in enumerate(QUALITATIVE_DIMENSIONS)]
if "quantitative_dimensions" not in st.session_state: st.session_state.quantitative_dimensions=[{**d,"id":f"n{i}"} for i,d in enumerate(QUANTITATIVE_DIMENSIONS)]
if "scoring_weights" not in st.session_state or "qualitative_dimensions" not in st.session_state.scoring_weights: st.session_state.scoring_weights={"qualitative_dimensions":st.session_state.qualitative_dimensions,"quantitative_dimensions":st.session_state.quantitative_dimensions,"groups":GROUP_DEFAULT_WEIGHTS}
with st.sidebar:
    st.header("流程导航")
    if "workflow_page" not in st.session_state: st.session_state.workflow_page=STEPS[0]
    page=st.radio("功能区",STEPS,key="workflow_page",label_visibility="collapsed")
    st.divider(); st.caption("所有筛选阈值与评分权重均为演示参数，可按项目情况调整。")
    exact=st.checkbox("行业必须一致",False)
    pool_min_size=st.number_input("每家被投企业候选池最低数量",min_value=1,max_value=50,value=5,step=1,help="同行业候选不足时，系统仅在有行业大类或产业链关系字段的情况下逐级扩池；仍不足会提示人工补充，不影响后续流程。")
    rev_min,rev_max=st.slider("收入规模倍数范围",0.1,8.0,(0.3,3.0),0.1)
    asset_min,asset_max=st.slider("总资产规模倍数范围",0.1,10.0,(0.2,5.0),0.1)
    growth_tol=st.slider("收入增速差异容忍度（百分点）",0,100,50)/100
    margin_tol=st.slider("利润率差异容忍度（百分点）",0,100,30)/100
current_step=STEPS.index(page)
st.markdown('<div class="stepbar">'+''.join(f'<div class="stepitem {"active" if i==current_step else "done" if i<current_step else ""}">{label}</div>' for i,label in enumerate(["准备数据","规则筛选","AI分析与排名","人工复核","估值分析与导出"]))+'</div>',unsafe_allow_html=True)
tables=st.session_state.tables
if page.startswith("①"):
    st.subheader("上传数据并检查字段")
    st.info("演示数据完全虚构。Excel 请包含 investee（被投企业）、candidates（上市公司资料库）和 client_list（客户已选名单）三个工作表。系统会为每家被投企业分别建立候选池，再合并该企业对应的客户名单；不会生成全局笛卡尔积。")
    template_bytes=create_input_template()
    st.download_button("下载标准 Excel 模板",template_bytes,file_name="可比公司筛选与估值数据模板.xlsx",mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",help="模板包含筛选、人工复核和估值所需字段；表头批注提供中文字段说明。")
    uploaded=st.file_uploader("上传候选池工作簿（.xlsx）",type=["xlsx"])
    c1,c2=st.columns(2)
    if c1.button("使用虚构演示数据"):
        demo_path=ROOT/"sample_data"/"demo_input.xlsx"
        st.session_state.tables=read_workbook(demo_path)
        st.session_state.source_workbook_bytes=demo_path.read_bytes()
        st.session_state.source_workbook_name=demo_path.name
        st.rerun()
    if uploaded:
        uploaded_bytes=uploaded.getvalue()
        try:
            st.session_state.tables=read_workbook(BytesIO(uploaded_bytes));tables=st.session_state.tables
            st.session_state.source_workbook_bytes=uploaded_bytes
            st.session_state.source_workbook_name=uploaded.name
            st.success("已自动识别并载入工作簿。若原表字段名称不同，可展开下方字段映射进行调整。")
        except Exception as e:
            st.warning(f"自动识别未完成：{e}。请使用下方字段映射。")
        try:
            raw_tables=read_raw_workbook(BytesIO(uploaded_bytes))
            with st.expander("字段映射（适用于 Wind、东方财富或其他自定义表头）"):
                st.caption("分别选择三个数据表所在的工作表，再把原文件列映射到系统字段。必需字段必须全部映射；估值字段可以暂时留空，后续估值页会提示缺项。")
                sheet_names=list(raw_tables)
                sheet_defaults={"investee":"investee","candidates":"candidates","client_list":"client_list"}
                sheet_map={}
                sheet_cols=st.columns(3)
                for col,role,label in zip(sheet_cols,["investee","candidates","client_list"],["被投企业资料","上市公司资料库","客户已有名单"]):
                    default=next((name for name in sheet_names if name.casefold()==sheet_defaults[role]),sheet_names[min(len(sheet_map),len(sheet_names)-1)])
                    sheet_map[role]=col.selectbox(label,sheet_names,index=sheet_names.index(default),key=f"map_sheet_{role}_{hashlib.sha1(uploaded_bytes).hexdigest()[:8]}")
                edited_mappings={}
                valuation_mapping_fields=["market_cap","cash","interest_bearing_debt","minority_interest","preferred_stock","non_operating_investments","ebitda","net_profit","book_equity","reporting_period","valuation_date","currency","amount_unit"]
                for role,label in [("investee","被投企业字段"),("candidates","候选公司字段"),("client_list","客户名单字段")]:
                    source=raw_tables[sheet_map[role]]
                    suggested=suggest_column_mapping(source,role)
                    fields=list(REQUIRED[role])
                    if role in {"investee","candidates"}: fields += [f for f in valuation_mapping_fields if f not in fields]
                    mapping_frame=pd.DataFrame([{"系统字段":field,"字段含义":FIELD_LABELS.get(field,field),"是否必需":"是" if field in REQUIRED[role] else "否","来源列":suggested.get(field,"")} for field in fields])
                    with st.expander(label):
                        edited=st.data_editor(mapping_frame,hide_index=True,width="stretch",disabled=["系统字段","字段含义","是否必需"],column_config={"来源列":st.column_config.SelectboxColumn("来源列",options=[""]+[str(c) for c in source.columns])},key=f"mapping_editor_{role}_{hashlib.sha1(uploaded_bytes).hexdigest()[:8]}")
                    edited_mappings[role]={str(row["系统字段"]):str(row["来源列"]) for _,row in edited.iterrows() if str(row["来源列"]).strip()}
                if st.button("应用字段映射并载入",type="primary",key="apply_field_mapping"):
                    mapped=build_tables_from_mapping(raw_tables,sheet_map,edited_mappings)
                    st.session_state.tables=mapped; tables=mapped
                    st.session_state.source_workbook_bytes=uploaded_bytes
                    st.session_state.source_workbook_name=uploaded.name
                    for stale_key in ("task_analysis","rule_tasks","candidate_pool","candidate_pool_summary","final_workbook_result","valuation_export_result"):
                        st.session_state.pop(stale_key,None)
                    st.success("字段映射已应用，后续流程将统一使用这一次上传的数据。")
        except Exception as mapping_error:
            st.error(f"无法读取工作簿用于字段映射：{mapping_error}")
    if tables:
        st.caption(f"被投企业 {len(tables['investee'])} 家 · 候选公司 {len(tables['candidates'])} 家 · 客户名单 {len(tables['client_list'])} 条")
        q=quality_report(tables)
        if q.empty: st.success("必需字段检查通过；空值仍需结合具体字段人工判断。")
        else: st.warning(f"发现 {len(q)} 项字段缺失或空值。缺失数据默认标记风险，不自动排除。")
        st.dataframe(q,width="stretch",hide_index=True)
        with st.expander("查看被投企业资料"): st.dataframe(tables["investee"],width="stretch",hide_index=True)
        with st.expander("查看上市公司资料库（候选池的数据来源）"):
            preview=[c for c in ["company_name","industry","stock_code","market","revenue","total_assets","revenue_growth","valuation_multiple","valuation_multiple_type"] if c in tables["candidates"].columns]
            st.dataframe(tables["candidates"][preview],width="stretch",hide_index=True,height=360)
            st.caption("这里是候选公司资料库。系统按每家被投企业的行业分别匹配，只有形成绑定关系的公司才进入后续规则筛选；目前未连接 Wind。")
        with st.expander("查看客户已选名单"):
            st.dataframe(tables["client_list"],width="stretch",hide_index=True)
            st.caption("客户名单会按被投企业合并到系统候选池。即使未通过行业或规模规则，也会保留到AI分析和人工复核，以便给出明确结论。")
elif not tables:
    st.warning("请先上传工作簿或载入演示数据。")
elif page.startswith("②"):
    st.subheader("定量规则筛选与分组评分权重")
    st.caption("定量硬筛和财务相似度由程序按数值计算；定性业务比较由 AI 基于对应原文和证据完成。以下维度与权重均可调整，属于演示参数，不是正式审计标准。")
    st.info("“通过当前规则筛选”只表示未触发当前设置的硬性排除条件，不代表最终可比或建议纳入。“当前规则未发现需提示事项”表示没有发现规模超阈值、行业差异、关键数据缺失或估值异常等提示。行业不同默认只是提示；只有勾选侧边栏的“行业必须一致”后，行业不一致才会被规则排除。")
    qualitative_fields=[c for c in tables["investee"].columns if c in tables["candidates"].columns and tables["investee"][c].dtype==object and c not in {"company_id","company_name","stock_code","market"}]
    quantitative_fields=[c for c in tables["investee"].columns if c in tables["candidates"].columns and c not in {"company_id","company_name","industry","company_description","main_business","products","application_scenarios","customer_groups","business_model","stock_code","market","source_reference","valuation_multiple_type","workpaper_file","sheet_name","cell_address"} and (pd.api.types.is_numeric_dtype(tables["investee"][c]) or pd.api.types.is_numeric_dtype(tables["candidates"][c]))]
    st.markdown("#### AI 定性业务相似度维度")
    st.caption("每项定性维度可自定义名称、权重和资料来源字段。AI 只比较所选文本字段，并需给出可回查证据。")
    qualitative_dims=st.session_state.qualitative_dimensions
    for idx,dim in enumerate(list(qualitative_dims)):
        cols=st.columns([2,4,1,1])
        dim["name"]=cols[0].text_input("维度名称",dim["name"],key=f"qname_{dim['id']}")
        chosen=[f for f in dim.get("fields",[]) if f in qualitative_fields]
        dim["fields"]=cols[1].multiselect("比较资料字段",qualitative_fields,default=chosen,key=f"qfields_{dim['id']}")
        dim["weight"]=cols[2].number_input("权重",0,100,int(dim.get("weight",10)),key=f"qweight_{dim['id']}")
        if cols[3].button("删除",key=f"qdelete_{dim['id']}"):
            st.session_state.qualitative_dimensions=[x for x in qualitative_dims if x["id"]!=dim["id"]]; st.rerun()
    if st.button("＋ 增加定性维度",key="add_qual_dimension"):
        st.session_state.qualitative_dimensions.append({"id":f"qcustom_{uuid.uuid4().hex[:8]}","name":"自定义定性维度","fields":qualitative_fields[:1],"weight":10}); st.rerun()
    st.write(f"定性维度权重合计：{sum(int(d.get('weight',0)) for d in qualitative_dims)}（组内自动归一化）")
    st.markdown("#### 程序定量经营相似度维度")
    st.caption("默认包含收入/资产规模、收入增长率、毛利率、EBITDA 利润率和资产负债率。相对规模按倍数差异评分，其他数值按可配置容忍差异评分；空值保留为资料不足。")
    quantitative_dims=st.session_state.quantitative_dimensions
    for dim in list(quantitative_dims):
        cols=st.columns([2,3,2,1,1])
        dim["name"]=cols[0].text_input("维度名称",dim["name"],key=f"nname_{dim['id']}")
        available_fields=quantitative_fields+(["debt_to_assets"] if "debt_to_assets" not in quantitative_fields else [])
        dim["fields"]=cols[1].multiselect("计算字段",available_fields,default=[f for f in dim.get("fields",[]) if f in available_fields],key=f"nfields_{dim['id']}")
        dim["method"]=cols[2].selectbox("计算方式",["tolerance","relative"],index=1 if dim.get("method")=="relative" else 0,format_func=lambda v:"相对规模倍数" if v=="relative" else "差异容忍度",key=f"nmethod_{dim['id']}")
        dim["tolerance"]=cols[3].number_input("容忍差异",min_value=0.0,max_value=10.0,value=float(dim.get("tolerance",.3)),step=.05,key=f"ntol_{dim['id']}")
        dim["weight"]=cols[4].number_input("权重",0,100,int(dim.get("weight",10)),key=f"nweight_{dim['id']}")
        if st.button("删除",key=f"ndelete_{dim['id']}"):
            st.session_state.quantitative_dimensions=[x for x in quantitative_dims if x["id"]!=dim["id"]]; st.rerun()
    if st.button("＋ 增加定量维度",key="add_quant_dimension"):
        st.session_state.quantitative_dimensions.append({"id":f"ncustom_{uuid.uuid4().hex[:8]}","name":"自定义定量维度","fields":quantitative_fields[:1],"method":"tolerance","tolerance":.3,"weight":10}); st.rerun()
    st.write(f"定量维度权重合计：{sum(int(d.get('weight',0)) for d in quantitative_dims)}（组内自动归一化）")
    qualitative_share=st.slider("综合得分中的定性分析占比（定量占比自动补足至100%）",0,100,GROUP_DEFAULT_WEIGHTS["定性分析"],5)
    group_weights={"定性分析":qualitative_share,"定量分析":100-qualitative_share}
    st.session_state["scoring_weights"]={"qualitative_dimensions":qualitative_dims,"quantitative_dimensions":quantitative_dims,"groups":group_weights}
    st.caption(f"综合权重：定性 {group_weights['定性分析']}% · 定量 {group_weights['定量分析']}%；程序定量规则仍由侧边栏阈值独立控制。")
    configured_names=[d["name"].strip() for d in qualitative_dims+quantitative_dims]
    has_duplicate_names=len(configured_names)!=len(set(configured_names)) or any(not n for n in configured_names)
    if has_duplicate_names: st.error("维度名称不能为空或重复，请修改后再继续。")
    inv=tables["investee"]
    if st.button("对全部被投企业运行规则筛选",type="primary",key="run_all_rules",disabled=has_duplicate_names):
        candidate_pool,pool_summary=build_candidate_pool(inv,tables["candidates"],tables["client_list"],min_pool_size=int(pool_min_size))
        st.session_state["candidate_pool"]=candidate_pool
        st.session_state["candidate_pool_summary"]=pool_summary
        st.session_state["candidate_pool_min_size"]=int(pool_min_size)
        st.session_state["rule_tasks"]=build_rule_tasks(inv,tables["candidates"],{"revenue_min":rev_min,"revenue_max":rev_max,"asset_ratio_min":asset_min,"asset_ratio_max":asset_max,"growth_diff":growth_tol,"margin_diff":margin_tol,"industry_exact":exact},candidate_pool=candidate_pool)
        st.session_state.pop("task_analysis",None)
    rule_tasks=st.session_state.get("rule_tasks")
    if rule_tasks is None:
        st.info("调整好侧边栏筛选条件和评分权重后，点击上方按钮；系统会一次性生成所有被投企业的候选配对清单。")
    else:
        pool_summary=st.session_state.get("candidate_pool_summary",pd.DataFrame())
        if not pool_summary.empty:
            shortages=pool_summary.loc[pool_summary["候选池状态"]=="候选不足，建议人工补充"]
            missing_clients=pool_summary.loc[pool_summary["客户名单缺失公司"].astype(str).str.strip()!=""]
            configured_pool_min=int(st.session_state.get("candidate_pool_min_size",pool_min_size))
            if not shortages.empty:
                names="、".join(shortages["被投企业"].astype(str))
                st.warning(f"{len(shortages)} 家被投企业的系统候选池少于设定的 {configured_pool_min} 家：{names}。请考虑人工补充候选公司；已有候选及客户指定公司仍可继续规则筛选、AI分析和人工复核。")
            if not missing_clients.empty:
                details="；".join(f"{row['被投企业']}：{row['客户名单缺失公司']}" for _,row in missing_clients.iterrows())
                st.warning(f"客户名单中的以下公司未在 candidates 资料库中找到，暂时无法分析：{details}")
            with st.expander("查看各被投企业候选池构建结果"):
                st.dataframe(pool_summary,width="stretch",hide_index=True)
        passing=int(rule_tasks.rule_eligible.sum())
        st.metric("候选配对总数",len(rule_tasks),f"通过当前规则 {passing} 组")
        preview=rule_tasks[["investee_company_name","company_name","industry","rule_eligible","rule_flags"]].rename(columns={"investee_company_name":"被投企业","company_name":"候选公司","industry":"行业","rule_eligible":"通过当前规则筛选","rule_flags":"规则筛选提示"})
        st.dataframe(preview,width="stretch",hide_index=True,height=520)
elif page.startswith("③"):
    st.subheader("业务相似度、候选排名与客户名单差异")
    with st.expander("⚙️ 配置 AI 模型接口",expanded=not current_llm_client().configured):
        st.caption("填写公司批准的 OpenAI 兼容接口信息。密钥只保存在当前应用进程内存，不会写入项目文件；客户资料会发送至此接口，请先确认数据授权与合规要求。")
        cfg=st.session_state.llm_config
        with st.form("llm_settings_form"):
            api_key_input=st.text_input("API Key（LLM_API_KEY）",value=cfg.get("api_key",""),type="password",placeholder="粘贴已获批的模型密钥")
            base_url_input=st.text_input("接口地址（LLM_BASE_URL）",value=cfg.get("base_url","https://api.openai.com/v1"),placeholder="例如 https://api.openai.com/v1")
            model_input=st.text_input("模型名称（LLM_MODEL）",value=cfg.get("model",""),placeholder="填写接口支持的模型名")
            thinking_input=st.selectbox("推理模式",["disabled","enabled"],index=1 if cfg.get("thinking")=="enabled" else 0,format_func=lambda x:"关闭（更快、更省 Token，推荐先测试）" if x=="disabled" else "开启（推理更深入，但更慢、更贵）")
            save_col,test_col=st.columns(2)
            save_llm=save_col.form_submit_button("保存配置")
            test_llm=test_col.form_submit_button("测试连接")
        if save_llm or test_llm:
            old_config=st.session_state.llm_config.copy()
            new_config={"api_key":api_key_input.strip(),"base_url":base_url_input.strip(),"model":model_input.strip(),"thinking":thinking_input}
            st.session_state.llm_config=new_config
            if api_key_input.strip(): os.environ["LLM_API_KEY"]=api_key_input.strip()
            else: os.environ.pop("LLM_API_KEY",None)
            if base_url_input.strip(): os.environ["LLM_BASE_URL"]=base_url_input.strip()
            if model_input.strip(): os.environ["LLM_MODEL"]=model_input.strip()
            else: os.environ.pop("LLM_MODEL",None)
            os.environ["LLM_THINKING"]=thinking_input
            configured_client=current_llm_client()
            try:
                configured_client.validate_config()
            except Exception as exc:
                st.error(f"配置无效：{exc}")
            else:
                if new_config != old_config:
                    st.session_state.pop("task_analysis",None)
                    st.session_state.pop("run_log",None)
                if save_llm:
                    st.success("配置已保存在当前应用进程内存。配置发生变化时，旧的演示/模型结果已清除，请重新点击分析按钮。")
                if test_llm:
                    try:
                        configured_client.test_connection()
                        st.success(f"连接测试成功：接口已实际调用模型 {configured_client.model}。")
                    except Exception as exc:
                        st.error(f"连接测试失败：{exc}")
        if current_llm_client().configured: st.success(f"当前已配置模型：{current_llm_client().model} · 接口：{current_llm_client().base_url}")
        else: st.warning("尚未配置完整接口。当前将使用虚构数据的本地演示分析；配置接口后自动切换为真实模型分析。")
    st.info("真实模型会逐项比较业务资料并撰写可比性原因分析。未配置接口时仍可完整体验演示流程，演示结论仅针对虚构数据。")
    inv=tables["investee"]; rule_tasks=st.session_state.get("rule_tasks")
    if rule_tasks is None:
        st.warning("请先到第②步对全部被投企业运行规则筛选，生成集中任务清单。")
    elif rule_tasks.empty:
        st.warning("当前规则下没有候选配对。请检查筛选阈值或数据。")
    else:
        client_rows=tables["client_list"]
        client_pairs=set(zip(client_rows.investee_company_name.astype(str).str.casefold(),client_rows.comparable_company_name.astype(str).str.casefold())) if not client_rows.empty else set()
        client_selected=rule_tasks.apply(lambda r:(str(r["investee_company_name"]).casefold(),str(r["company_name"]).casefold()) in client_pairs,axis=1)
        # AI analyzes companies that pass current rules, plus client picks that failed rules so disagreements remain visible.
        ai_tasks=rule_tasks[rule_tasks.rule_eligible | client_selected].copy()
        client=current_llm_client()
        run_one_col,run_all_col=st.columns([2,3])
        run_one=run_one_col.button("先试跑 1 组",key="run_one_ai",help="先验证真实模型返回格式和分析质量，避免批量调用后才发现问题。")
        run_ai=run_all_col.button("分析全部待处理候选配对",type="primary",key="run_all_ai",help="对所有尚未生成或需要重新生成的候选配对执行 AI 分析。")
        if run_ai or run_one:
            analyzed=st.session_state.get("task_analysis",{})
            incomplete_ids={
                task_id for task_id,record in analyzed.items()
                if not str(record.get("comparability_analysis","")).strip()
                or bool(record.get("_analysis_failed"))
                or str(record.get("comparability_analysis","")).startswith("模型未返回完整原因分析")
                or (client.configured and record.get("model")!=client.model)
            }
            pending=ai_tasks[(~ai_tasks.task_id.isin(analyzed)) | ai_tasks.task_id.isin(incomplete_ids)]
            if run_one: pending=pending.head(1)
            if pending.empty: st.info("所有候选配对都已有分析结果。")
            else:
                progress=st.progress(0); logs=st.session_state.get("run_log",[])
                score_weights=st.session_state.scoring_weights
                qual_config=[{k:d[k] for k in ("name","fields","weight")} for d in score_weights["qualitative_dimensions"]]
                jobs=[]
                for _,task in pending.iterrows():
                    a=inv.loc[inv.company_name==task.investee_company_name].iloc[0].to_dict()
                    c=tables["candidates"].loc[tables["candidates"].company_name==task.company_name].iloc[0].to_dict()
                    jobs.append((task.to_dict(),a,c))

                def analyze_job(job):
                    d,a,c=job
                    try:
                        program_quantitative=quantitative_scores(a,c,score_weights["quantitative_dimensions"],growth_tol,margin_tol)
                        quantitative_context=[]
                        for dimension in score_weights["quantitative_dimensions"]:
                            fields=dimension.get("fields",[])
                            quantitative_context.append({
                                "dimension":dimension["name"],
                                "fields":fields,
                                "investee_values":{field:a.get(field) for field in fields},
                                "candidate_values":{field:c.get(field) for field in fields},
                                "method":dimension.get("method"),
                                "tolerance":dimension.get("tolerance"),
                                "program_score":program_quantitative.get(dimension["name"]),
                            })
                        rule_context={
                            "eligible":bool(d.get("rule_eligible")),
                            "flags":d.get("rule_flags",""),
                            "candidate_pool_source":d.get("pool_source",""),
                            "candidate_pool_reason":d.get("pool_reason",""),
                            "instruction":"候选来源仅用于说明分析背景，不能代替可比性证据；如属于扩池或客户指定，应结合两家公司资料判断其是否真正可比。",
                        }
                        result=client.analyze(a,c,qual_config,quantitative_comparison=quantitative_context,rule_screening=rule_context)
                        scores,breakdown=calculate_pair_scores(result.dimension_scores,a,c,score_weights,growth_tol,margin_tol)
                        d.update({"dimension_scores":scores,**breakdown,"qualitative_attributes":result.qualitative_attributes,"similarities":result.similarities,"differences":result.differences,"risk_flags":result.risk_flags,"evidence":result.evidence,"data_sufficiency":result.data_sufficiency,"ai_recommendation":result.recommendation,"recommendation_reason":result.recommendation_reason,"comparability_analysis":result.comparability_analysis,"model":result.model,"_analysis_failed":False})
                        log={"investee":d["investee_company_name"],"candidate":d["company_name"],"mode":result.model,"status":"成功"}
                    except Exception as e:
                        scores,breakdown=calculate_pair_scores({},a,c,score_weights,growth_tol,margin_tol)
                        d.update({"dimension_scores":scores,**breakdown,"qualitative_attributes":{},"similarities":[],"differences":["模型调用失败，定性业务相似度需人工判断"],"risk_flags":[str(e)[:300]],"evidence":[],"data_sufficiency":"不足","ai_recommendation":"资料不足","recommendation_reason":"模型调用失败或返回格式无效。","comparability_analysis":"模型未返回完整原因分析，请检查模型接口或补充人工判断。","model":client.model or "未配置","_analysis_failed":True})
                        log={"investee":d["investee_company_name"],"candidate":d["company_name"],"mode":client.model or "未配置","status":"失败","detail":str(e)[:300]}
                    comparison=compare_client_list(d["investee_company_name"],pd.DataFrame([d]),tables["client_list"]).iloc[0]
                    d["客户是否已选择"]=comparison["客户是否已选择"]; d["差异类型"]=comparison["差异类型"]
                    return d,log

                completed=0
                worker_count=1 if run_one else max(1,len(jobs))
                with ThreadPoolExecutor(max_workers=worker_count) as executor:
                    futures=[executor.submit(analyze_job,job) for job in jobs]
                    for future in as_completed(futures):
                        d,log=future.result()
                        analyzed[d["task_id"]]=d; logs.append(log)
                        if run_one: st.session_state["single_test_task_id"]=d["task_id"]
                        completed+=1; progress.progress(completed/len(jobs))
                if run_ai: st.session_state.pop("single_test_task_id",None)
                st.session_state["task_analysis"]=analyzed;st.session_state["run_log"]=logs
                failures=[item for item in logs[-len(pending):] if item.get("status")=="失败"]
                if failures:
                    st.error(f"本次有 {len(failures)} 组模型调用失败。首条错误：{failures[0].get('detail','未知错误')}")
                else:
                    st.success(f"已通过模型 {client.model if client.configured else 'Demo heuristic'} 完成 {len(pending)} 组分析。")
        analyzed=st.session_state.get("task_analysis",{})
        if not analyzed:
            st.info(f"规则筛选共发现 {len(rule_tasks)} 组配对；AI 将分析其中 {len(ai_tasks)} 组通过当前规则的候选，并保留客户已选但规则未通过的配对供重点复核。点击上方按钮后，结果会集中显示在一张任务大表中。")
        else:
            score_weights=st.session_state.scoring_weights
            for task_id,record in analyzed.items():
                investee_record=inv.loc[inv.company_name==record["investee_company_name"]].iloc[0].to_dict()
                candidate_record=tables["candidates"].loc[tables["candidates"].company_name==record["company_name"]].iloc[0].to_dict()
                prior_scores=record.get("dimension_scores",{})
                q_scores={d["name"]:prior_scores.get(d["name"]) for d in score_weights["qualitative_dimensions"]}
                combined_scores,breakdown=calculate_pair_scores(q_scores,investee_record,candidate_record,score_weights,growth_tol,margin_tol)
                record.update({"dimension_scores":combined_scores,**breakdown})
                analyzed[task_id]=record
            st.session_state["task_analysis"]=analyzed
            all_rows=pd.DataFrame(analyzed.values())
            f1,f2,f3=st.columns([2,2,3])
            investee_filter=f1.multiselect("筛选被投企业",sorted(all_rows.investee_company_name.unique()))
            recommendation_filter=f2.multiselect("筛选 AI 建议",sorted(all_rows.ai_recommendation.unique()))
            search=f3.text_input("搜索公司名称或差异提示",placeholder="输入被投企业、候选公司或差异关键词")
            visible=all_rows.copy()
            if investee_filter: visible=visible[visible.investee_company_name.isin(investee_filter)]
            if recommendation_filter: visible=visible[visible.ai_recommendation.isin(recommendation_filter)]
            if search:
                mask=visible[["investee_company_name","company_name","差异类型","comparability_analysis"]].astype(str).apply(lambda col:col.str.contains(search,case=False,regex=False)).any(axis=1)
                visible=visible[mask]
            st.caption(f"任务大表：显示 {len(visible)} / {len(all_rows)} 组被投企业—候选公司配对。点击任意一行查看完整资料和分析依据。")
            urgent=visible[visible["差异类型"].isin(["AI建议排除，客户已经纳入","AI建议纳入，客户没有纳入"])]
            st.warning(f"当前筛选下有 {len(urgent)} 组客户名单与 AI 建议不一致，需重点复核。")
            single_test_id=st.session_state.get("single_test_task_id")
            single_test=bool(single_test_id and len(visible)==1 and visible.iloc[0].get("task_id")==single_test_id)
            if single_test:
                test_row=visible.iloc[0]
                target_record=inv.loc[inv.company_name==test_row["investee_company_name"]].iloc[0]
                peer_record=tables["candidates"].loc[tables["candidates"].company_name==test_row["company_name"]].iloc[0]
                profile_fields=[("stock_code","证券代码"),("market","市场"),("industry","行业"),("company_description","公司简介"),("main_business","主营业务"),("products","产品"),("application_scenarios","应用场景"),("customer_groups","客户群体"),("business_model","商业模式"),("revenue","营业收入"),("total_assets","总资产"),("total_liabilities","总负债"),("debt_to_assets","资产负债率"),("revenue_growth","收入增长率"),("gross_margin","毛利率"),("ebitda_margin","EBITDA利润率"),("market_cap","市值"),("valuation_multiple","估值倍数"),("valuation_multiple_type","估值倍数类型"),("source_reference","资料来源")]
                def test_profile(record):
                    return "\n".join(f"{label}：{text_value(record.get(field)) or '资料未提供'}" for field,label in profile_fields)
                pair=(test_row["investee_company_name"],test_row["company_name"])
                saved=st.session_state.decisions.get(pair,{})
                if not isinstance(saved,dict): saved={}
                summary=pd.DataFrame([{
                    "被投企业":pair[0],
                    "被投企业资料":test_profile(target_record),
                    "候选公司":pair[1],
                    "候选公司资料":test_profile(peer_record),
                    "AI判断依据（证据）":"\n".join(str(item) for item in (test_row.get("evidence",[]) or [])),
                    "AI回复":test_row.get("comparability_analysis",""),
                    "AI建议":test_row.get("ai_recommendation","需要人工判断"),
                    "人工复核状态":saved.get("review_status","待复核"),
                    "复核意见":saved.get("review_note",""),
                    "合理性分析初稿":saved.get("analysis_draft",""),
                    "底稿文件":saved.get("workpaper_file",text_value(peer_record.get("workpaper_file"))),
                    "工作表":saved.get("sheet_name",text_value(peer_record.get("sheet_name"))),
                    "单元格":saved.get("cell_address",text_value(peer_record.get("cell_address"))),
                }])
                table_height=260
                column_config={"被投企业资料":st.column_config.TextColumn(width="large"),"候选公司资料":st.column_config.TextColumn(width="large"),"AI判断依据（证据）":st.column_config.TextColumn(width="large"),"AI回复":st.column_config.TextColumn(width="large")}
                st.markdown("#### 单条试跑结果（与第四步复核表字段一致）")
            else:
                summary=visible[["investee_company_name","company_name","定性得分","定量得分","综合得分","资料覆盖率","ai_recommendation","comparability_analysis","客户是否已选择","差异类型","rule_flags"]].rename(columns={"investee_company_name":"被投企业","company_name":"候选公司","定性得分":"定性得分","定量得分":"定量得分","资料覆盖率":"资料覆盖率(%)","ai_recommendation":"AI建议","comparability_analysis":"AI原因分析","客户是否已选择":"客户已选","差异类型":"名单差异","rule_flags":"规则提示"}).reset_index(drop=True)
                table_height=500
                column_config={"综合得分":st.column_config.NumberColumn(format="%.1f"),"AI原因分析":st.column_config.TextColumn(width="large")}
            event=st.dataframe(summary,width="stretch",hide_index=True,height=table_height,on_select="rerun",selection_mode="single-row",key="all_pair_task_table",column_config=column_config)
            if len(visible)==1:
                single=visible.iloc[0]
                st.markdown("#### AI 完整原因分析")
                st.write(single.get("comparability_analysis") or "模型未返回完整原因分析。")
                st.caption(f"AI建议：{single.get('ai_recommendation','资料不足')} · {single.get('recommendation_reason','')}")
            selected_rows=event.selection.rows
            if selected_rows:
                row=visible.reset_index(drop=True).iloc[selected_rows[0]]
                name=row["investee_company_name"]; chosen=row["company_name"]
                st.markdown(f"### {name}　×　{chosen}")
                left,right=st.columns(2)
                with left:
                    st.markdown("**各维度得分**")
                    weight_settings=st.session_state.scoring_weights
                    weight_lookup={d["name"]:d["weight"] for d in weight_settings["qualitative_dimensions"]+weight_settings["quantitative_dimensions"]}
                    qualitative_names={d["name"] for d in weight_settings["qualitative_dimensions"]}
                    dims=pd.DataFrame([{"类型":"定性" if k in qualitative_names else "定量","维度":k,"得分":v if v is not None else "资料不足","组内权重":weight_lookup.get(k,0)} for k,v in row.get("dimension_scores",{}).items()])
                    st.dataframe(dims,width="stretch",hide_index=True)
                    st.caption(f"定性覆盖率 {row.get('定性覆盖率',0):.1f}% · 定量覆盖率 {row.get('定量覆盖率',0):.1f}%；缺失维度不按0分处理，但会降低资料覆盖率。")
                    st.markdown("**主要相似点**")
                    for item in row.get("similarities",[]) or ["资料不足，无法总结相似点。"]: st.markdown(f"- {item}")
                with right:
                    st.markdown("**主要差异**")
                    for item in row.get("differences",[]) or ["未发现文本差异摘要；仍需人工核对原始资料。"]: st.markdown(f"- {item}")
                    st.markdown("**建议及数据充分性**")
                    st.write(f"{row.get('ai_recommendation','资料不足')} · {row.get('data_sufficiency','不足')}")
                    st.caption(row.get("recommendation_reason",""))
                    st.markdown("**规则提示**")
                    st.write(row.get("rule_flags",""))
                with st.expander("查看可追溯证据"):
                    for item in row.get("evidence",[]) or ["当前输出未提供可核验摘录。"]: st.markdown(f"- {item}")
                with st.expander("查看 AI 提取的定性特征"):
                    attributes=row.get("qualitative_attributes",{}) or {}
                    for side,label in [("investee","被投企业"),("candidate","候选公司")]:
                        st.markdown(f"**{label}**")
                        st.json(attributes.get(side,{"提示":"模型未返回结构化特征；请核对原始资料。"}))
                with st.expander("并排查看被投企业和候选公司原始资料",expanded=True):
                    investee_row=inv.loc[inv.company_name==name].iloc[0]
                    candidate_row=tables["candidates"].loc[tables["candidates"].company_name==chosen].iloc[0]
                    lcol,rcol=st.columns(2)
                    for col,title,record in [(lcol,"被投企业",investee_row),(rcol,"候选公司",candidate_row)]:
                        with col:
                            st.markdown(f"**{title}：{record.get('company_name','') }**")
                            for field,label in [("industry","行业"),("company_description","公司简介"),("main_business","主营业务"),("products","产品"),("application_scenarios","应用场景"),("customer_groups","客户群体"),("business_model","商业模式")]:
                                st.markdown(f"**{label}：**{record.get(field,'—')}")
                st.session_state.selected_analysis=name
            else:
                st.info("点击任务大表任意一行，展开该组配对的维度评分、相似点、差异、证据和两家公司资料。")
elif page.startswith("④"):
    st.subheader("人工确认、合理性分析与结果导出")
    analyzed_map=st.session_state.get("task_analysis",{})
    if not analyzed_map:
        st.info("请先到第③步分析候选配对，完成后这里会显示全量人工复核任务大表。")
    else:
        analyzed_all=pd.DataFrame(analyzed_map.values())
        is_demo="source_reference" in tables["candidates"] and tables["candidates"]["source_reference"].astype(str).str.contains("虚构演示数据").all()
        review_rows=[]
        for _,task in analyzed_all.iterrows():
            name=task["investee_company_name"]; candidate=task["company_name"]
            prior=st.session_state.decisions.get((name,candidate),{})
            if not isinstance(prior,dict): prior={}
            investee=tables["investee"].loc[tables["investee"].company_name==name].iloc[0]
            candidate_row=tables["candidates"].loc[tables["candidates"].company_name==candidate].iloc[0]
            def profile(record):
                fields=[("stock_code","证券代码"),("market","市场"),("industry","行业"),("company_description","公司简介"),("main_business","主营业务"),("products","产品"),("application_scenarios","应用场景"),("customer_groups","客户群体"),("business_model","商业模式"),("revenue","营业收入"),("total_assets","总资产"),("total_liabilities","总负债"),("debt_to_assets","资产负债率"),("revenue_growth","收入增长率"),("gross_margin","毛利率"),("ebitda_margin","EBITDA利润率"),("market_cap","市值"),("valuation_multiple","估值倍数"),("valuation_multiple_type","估值倍数类型"),("source_reference","资料来源")]
                return "\n".join(f"{label}：{text_value(record.get(field)) or '资料未提供'}" for field,label in fields)
            ai_reply=task.get("comparability_analysis","") or "尚未生成完整原因分析。请检查模型配置并重新运行该任务。"
            ai_evidence="\n".join(str(item) for item in (task.get("evidence",[]) or [])) or "AI未返回可追溯证据；需人工核对原始资料。"
            candidate_idx=int(tables["candidates"].index[tables["candidates"].company_name==candidate][0])+2
            rationale=prior.get("analysis_draft","")
            review_rows.append({"task_id":task["task_id"],"被投企业":name,"被投企业资料":profile(investee),"候选公司":candidate,"候选公司资料":profile(candidate_row),"AI判断依据（证据）":ai_evidence,"AI回复":ai_reply,"AI建议":task.get("ai_recommendation","需要人工判断"),
                "人工复核状态":prior.get("review_status",PENDING),"人工最终结论":prior.get("final_decision",""),"复核意见":prior.get("review_note",""),"合理性分析初稿":rationale,
                "底稿文件":prior.get("workpaper_file",text_value(candidate_row.get("workpaper_file")) or ("demo_workpaper.xlsx" if is_demo else "")),
                "工作表":prior.get("sheet_name",text_value(candidate_row.get("sheet_name")) or (name if is_demo else "")),
                "单元格":prior.get("cell_address",text_value(candidate_row.get("cell_address")) or (f"D{candidate_idx}" if is_demo else ""))})
        review_frame=pd.DataFrame(review_rows)
        st.caption(f"复核任务大表：共 {len(review_frame)} 组配对。先勾选行，再批量修改复核状态或最终结论；单条记录可在“操作”中详细复核。")
        if not current_llm_client().configured:
            st.info("当前为虚构数据的本地演示模式。复核和重试功能可以正常体验；正式项目需配置经批准的模型接口。")

        def save_decision(task_id,status,note,draft,workpaper_file,sheet_name,cell_address,final_decision=""):
            base=analyzed_map[task_id]
            st.session_state.decisions[(base["investee_company_name"],base["company_name"])]= {
                "review_status":status,"final_decision":final_decision,"review_note":note,"analysis_draft":draft,
                "workpaper_file":workpaper_file,"sheet_name":sheet_name,"cell_address":cell_address}

        @st.dialog("人工复核与 AI 重试",width="large")
        def review_and_retry(task_id):
            base=analyzed_map[task_id]
            pair=(base["investee_company_name"],base["company_name"])
            saved=st.session_state.decisions.get(pair,{})
            row=review_frame.loc[review_frame.task_id==task_id].iloc[0]
            st.markdown(f"### {pair[0]} × {pair[1]}")
            with st.expander("查看 AI 完整分析与判断证据",expanded=True):
                st.markdown(row["AI回复"])
                st.markdown("**判断证据**")
                st.text(row["AI判断依据（证据）"])
            saved_status=REVIEWED if saved.get("review_status")=="复核通过" else saved.get("review_status",PENDING)
            if saved_status not in {PENDING,REVIEWED}: saved_status=PENDING
            status_options=[PENDING,REVIEWED]
            status=st.selectbox("人工复核状态",status_options,index=status_options.index(saved_status),key=f"dialog_status_{task_id}")
            decision_options=["未决定",INCLUDED,EXCLUDED]
            saved_decision=saved.get("final_decision","") or "未决定"
            if saved_decision=="最终纳入": saved_decision=INCLUDED
            if saved_decision not in decision_options: saved_decision="未决定"
            final_decision=st.selectbox("人工最终结论",decision_options,index=decision_options.index(saved_decision),key=f"dialog_decision_{task_id}")
            note=st.text_area("复核意见",value=saved.get("review_note",""),height=120,placeholder="例如：请重点比较两家公司的商业模式，并说明实施服务占比差异对毛利率及估值倍数的影响。",key=f"dialog_note_{task_id}")
            draft=st.text_area("合理性分析初稿（可人工修改）",value=saved.get("analysis_draft",""),height=150,key=f"dialog_draft_{task_id}")
            w1,w2,w3=st.columns(3)
            workpaper_file=w1.text_input("底稿文件",value=saved.get("workpaper_file",row["底稿文件"]),key=f"dialog_file_{task_id}")
            sheet_name=w2.text_input("工作表",value=saved.get("sheet_name",row["工作表"]),key=f"dialog_sheet_{task_id}")
            cell_address=w3.text_input("单元格",value=saved.get("cell_address",row["单元格"]),key=f"dialog_cell_{task_id}")
            b1,b2,b3=st.columns(3)
            if b1.button("保存本行复核",type="primary",key=f"save_row_{task_id}"):
                normalized_decision="" if final_decision=="未决定" else final_decision
                normalized_status=REVIEWED if normalized_decision in {INCLUDED,EXCLUDED} else status
                if normalized_status==PENDING: normalized_decision=""
                save_decision(task_id,normalized_status,text_value(note),text_value(draft),text_value(workpaper_file),text_value(sheet_name),text_value(cell_address),normalized_decision)
                st.success("本行复核内容已保存。")
            retry_disabled=not text_value(note)
            if b2.button("按意见重新生成 AI 分析",disabled=retry_disabled,key=f"retry_row_{task_id}"):
                save_decision(task_id,PENDING,text_value(note),text_value(draft),text_value(workpaper_file),text_value(sheet_name),text_value(cell_address),"")
                investee=tables["investee"].loc[tables["investee"].company_name==pair[0]].iloc[0].to_dict()
                candidate=tables["candidates"].loc[tables["candidates"].company_name==pair[1]].iloc[0].to_dict()
                try:
                    score_weights=st.session_state.scoring_weights
                    dimensions=[{k:d[k] for k in ("name","fields","weight")} for d in score_weights["qualitative_dimensions"]]
                    revised=current_llm_client().revise_comparability_analysis(investee,candidate,base,text_value(note),dimensions)
                    new_scores,breakdown=calculate_pair_scores(revised.dimension_scores,investee,candidate,score_weights,growth_tol,margin_tol)
                    base.update({"dimension_scores":new_scores,**breakdown,"qualitative_attributes":revised.qualitative_attributes,"similarities":revised.similarities,"differences":revised.differences,"risk_flags":revised.risk_flags,"evidence":revised.evidence,"data_sufficiency":revised.data_sufficiency,"ai_recommendation":revised.recommendation,"recommendation_reason":revised.recommendation_reason,"comparability_analysis":revised.comparability_analysis,"model":revised.model})
                    analyzed_map[task_id]=base; st.session_state.task_analysis=analyzed_map
                    save_decision(task_id,PENDING,text_value(note),"",text_value(workpaper_file),text_value(sheet_name),text_value(cell_address),"")
                    st.session_state["rewrite_notice"]="该行已按复核意见重新生成，请检查新版 AI 分析及证据。"
                    st.rerun()
                except Exception as exc: st.error(f"重新生成失败：{exc}")
            rationale_disabled=(final_decision!=INCLUDED)
            if b3.button("生成纳入分析初稿",disabled=rationale_disabled,key=f"draft_row_{task_id}"):
                investee=tables["investee"].loc[tables["investee"].company_name==pair[0]].iloc[0].to_dict()
                candidate=tables["candidates"].loc[tables["candidates"].company_name==pair[1]].iloc[0].to_dict()
                try:
                    rewritten=current_llm_client().generate_rationale(investee,candidate,base,text_value(note),text_value(draft))
                    save_decision(task_id,REVIEWED,text_value(note),rewritten,text_value(workpaper_file),text_value(sheet_name),text_value(cell_address),INCLUDED)
                    st.session_state["rewrite_notice"]="该行合理性分析初稿已生成，请人工复核。"
                    st.rerun()
                except Exception as exc: st.error(f"初稿生成失败：{exc}")
            if retry_disabled: st.caption("填写复核意见后，才能按意见重新生成本条 AI 分析。")

        @st.dialog("企业原始资料",width="large")
        def show_company_details(task_id,side):
            row=review_frame.loc[review_frame.task_id==task_id].iloc[0]
            if side=="investee":
                st.markdown(f"#### 被投企业：{row['被投企业']}")
                st.text(row["被投企业资料"])
            else:
                st.markdown(f"#### 候选公司：{row['候选公司']}")
                st.text(row["候选公司资料"])

        @st.dialog("AI 可比性原因分析",width="large")
        def show_analysis_details(task_id):
            row=review_frame.loc[review_frame.task_id==task_id].iloc[0]
            st.markdown(f"#### {row['被投企业']} × {row['候选公司']}")
            st.markdown("#### AI回复")
            st.markdown(row["AI回复"])
            with st.expander("AI判断依据（原始证据）"):
                st.text(row["AI判断依据（证据）"])
            st.markdown("#### 两家公司资料对比")
            investee_record=tables["investee"].loc[tables["investee"].company_name==row["被投企业"]].iloc[0]
            candidate_record=tables["candidates"].loc[tables["candidates"].company_name==row["候选公司"]].iloc[0]
            comparison_fields=[("industry","行业"),("main_business","主营业务"),("products","产品"),("application_scenarios","应用场景"),("customer_groups","客户群体"),("business_model","商业模式"),("revenue","营业收入"),("revenue_growth","收入增长率"),("gross_margin","毛利率"),("ebitda_margin","EBITDA利润率"),("debt_to_assets","资产负债率")]
            comparison_rows=[]
            for field,label in comparison_fields:
                left=text_value(investee_record.get(field)) or "资料未提供"
                right=text_value(candidate_record.get(field)) or "资料未提供"
                comparison_rows.append({"对比维度":label,"被投企业":left,"候选公司":right})
            st.dataframe(pd.DataFrame(comparison_rows),width="stretch",hide_index=True,height=430)

        if st.session_state.get("rewrite_notice"): st.success(st.session_state.pop("rewrite_notice"))

        status_values=review_frame["人工复核状态"].replace({"复核通过":REVIEWED})
        decision_values=review_frame["人工最终结论"].fillna("")
        metrics=st.columns(5)
        metrics[0].metric("总记录",len(review_frame))
        metrics[1].metric("已复核",int(status_values.eq(REVIEWED).sum()))
        metrics[2].metric("待复核",int((~status_values.eq(REVIEWED)).sum()))
        metrics[3].metric("已纳入",int(decision_values.eq(INCLUDED).sum()))
        metrics[4].metric("不纳入",int(decision_values.eq(EXCLUDED).sum()))

        filter_col,size_col,page_col=st.columns([5,1.2,1.2],vertical_alignment="bottom")
        with filter_col:
            search=st.text_input("搜索公司",placeholder="输入被投企业或候选公司名称",key="review_search")
        visible=review_frame
        if search.strip():
            token=search.strip().casefold()
            visible=visible[visible["被投企业"].astype(str).str.casefold().str.contains(token,regex=False) | visible["候选公司"].astype(str).str.casefold().str.contains(token,regex=False)]
        standard_ai_options=["建议纳入","建议排除","需要人工判断"]
        actual_ai_options=[text_value(value) for value in review_frame["AI建议"].dropna().unique() if text_value(value)]
        ai_filter_options=["全部"]+standard_ai_options+[value for value in actual_ai_options if value not in standard_ai_options]
        ai_filter=st.session_state.get("review_ai_filter","全部")
        if ai_filter not in ai_filter_options:
            ai_filter="全部"; st.session_state["review_ai_filter"]="全部"
        if ai_filter!="全部":
            visible=visible.loc[visible["AI建议"].astype(str).eq(ai_filter)]
        bulk_scope=visible.copy()
        with size_col:
            page_size=st.selectbox("每页显示",[10,20,50],index=1,key="review_page_size")
        page_count=max(1,(len(visible)+page_size-1)//page_size)
        if not 1<=int(st.session_state.get("review_page_no",1))<=page_count:
            st.session_state["review_page_no"]=1
        with page_col:
            page_no=st.number_input("页码",1,page_count,1,key="review_page_no")
        visible=visible.iloc[(page_no-1)*page_size:page_no*page_size]
        st.caption(f"当前筛选显示 {len(bulk_scope)} / {len(review_frame)} 条记录。全选和批量操作仅作用于当前筛选结果。")

        def selection_key(task_id): return f"review_select_{task_id}"
        def set_scope_selection(task_ids,widget_key):
            checked=bool(st.session_state.get(widget_key))
            for selected_task_id in task_ids:
                st.session_state[selection_key(selected_task_id)]=checked
        scope_ids=bulk_scope["task_id"].tolist()
        scope_signature=hashlib.sha1("|".join(scope_ids).encode("utf-8")).hexdigest()[:10]
        select_all_key=f"review_select_all_{scope_signature}"
        all_scope_selected=bool(scope_ids) and all(st.session_state.get(selection_key(task_id),False) for task_id in scope_ids)
        if st.session_state.get(select_all_key)!=all_scope_selected:
            st.session_state[select_all_key]=all_scope_selected
        selected_ids=[task_id for task_id in scope_ids if st.session_state.get(selection_key(task_id),False)]
        st.caption(f"批量操作：已选择 {len(selected_ids)} 条；只影响勾选记录，并可撤销。")

        def apply_bulk_review_action(action,label):
            snapshot={}
            for task_id in selected_ids:
                base=analyzed_map[task_id]
                pair=(base["investee_company_name"],base["company_name"])
                existing=st.session_state.decisions.get(pair)
                snapshot[pair]=dict(existing) if isinstance(existing,dict) else None
                st.session_state.decisions[pair]=apply_action(existing,action)
            st.session_state["review_bulk_undo"]={"snapshot":snapshot,"label":label}
            st.session_state["review_bulk_notice"]=f"已对 {len(selected_ids)} 条记录执行“{label}”。"

        action_cols=st.columns([1.25,1.25,1.15,1.15,1.15,1.1])
        actions=[
            ("标记已复核","mark_reviewed"),("恢复待复核","restore_pending"),
            ("设为纳入","include"),("设为不纳入","exclude"),("清除结论","clear_decision")]
        for col,(label,action) in zip(action_cols[:5],actions):
            if col.button(label,disabled=not selected_ids,key=f"bulk_{action}",type="primary" if action in {"mark_reviewed","include"} else "secondary"):
                apply_bulk_review_action(action,label); st.rerun()
        undo=st.session_state.get("review_bulk_undo")
        if action_cols[5].button("撤销上次操作",disabled=not undo,key="undo_bulk_review"):
            for pair,previous in undo["snapshot"].items():
                if previous is None: st.session_state.decisions.pop(pair,None)
                else: st.session_state.decisions[pair]=previous
            st.session_state["review_bulk_notice"]=f"已撤销“{undo['label']}”。"
            st.session_state.pop("review_bulk_undo",None); st.rerun()
        if st.session_state.get("review_bulk_notice"): st.success(st.session_state.pop("review_bulk_notice"))

        header=st.columns([.35,1.25,1.25,3,1,1,1.15])
        with header[0]:
            st.markdown('<span class="review-header-marker"></span>',unsafe_allow_html=True)
            st.checkbox(
                "全选",key=select_all_key,help=f"选择当前筛选结果中的全部 {len(scope_ids)} 条记录",
                disabled=not scope_ids,on_change=set_scope_selection,args=(scope_ids,select_all_key)
            )
        for col,label in zip([header[1],header[2],header[4],header[5],header[6]],["被投企业","候选公司","复核状态","最终结论","操作"]):
            col.markdown(f"**{label}**")
        with header[3]:
            st.selectbox(
                "AI建议",ai_filter_options,key="review_ai_filter",label_visibility="collapsed",
                format_func=lambda value: "AI建议（全部）" if value=="全部" else f"AI建议：{value}"
            )
        st.divider()
        for _,row in visible.iterrows():
            task_id=row["task_id"]; pair=(row["被投企业"],row["候选公司"]); saved=st.session_state.decisions.get(pair,{})
            cols=st.columns([.35,1.25,1.25,3,1,1,1.15],vertical_alignment="center")
            with cols[0]:
                st.checkbox("选择",key=selection_key(task_id),label_visibility="collapsed")
            with cols[1]:
                st.markdown(f"**{row['被投企业']}**")
                if st.button("查看资料",key=f"investee_detail_{task_id}"): show_company_details(task_id,"investee")
            with cols[2]:
                st.markdown(f"**{row['候选公司']}**")
                if st.button("查看资料",key=f"candidate_detail_{task_id}"): show_company_details(task_id,"candidate")
            with cols[3]:
                st.markdown(f"**AI建议：{row['AI建议']}**")
                preview=text_value(row["AI回复"])
                st.caption((preview[:180]+"…") if len(preview)>180 else preview)
                if st.button("查看完整AI分析",key=f"analysis_{task_id}"): show_analysis_details(task_id)
            with cols[4]:
                review_label=REVIEWED if saved.get("review_status") in {REVIEWED,"复核通过"} else PENDING
                st.markdown(status_badge(review_label,"review"),unsafe_allow_html=True)
            with cols[5]:
                decision_label=saved.get("final_decision") or ""
                st.markdown(status_badge(decision_label,"decision"),unsafe_allow_html=True)
            with cols[6]:
                if st.button("复核 / 重试",key=f"review_{task_id}"): review_and_retry(task_id)
            st.divider()
        result_rows=[]
        for task in analyzed_map.values():
            item=dict(task); investee_name=item["investee_company_name"]
            record=st.session_state.decisions.get((investee_name,item["company_name"]),{})
            if not isinstance(record,dict): record={}
            candidate_match=tables["candidates"].loc[tables["candidates"].company_name==item["company_name"]]
            if not candidate_match.empty: item["stock_code"]=text_value(candidate_match.iloc[0].get("stock_code"))
            item.update({"人工复核状态":record.get("review_status",PENDING),"人工最终结论":record.get("final_decision",""),"人工复核意见":record.get("review_note",""),"workpaper_file":record.get("workpaper_file",item.get("workpaper_file","")),"sheet_name":record.get("sheet_name",item.get("sheet_name","")),"cell_address":record.get("cell_address",item.get("cell_address",""))})
            if record.get("review_status") in {REVIEWED,"复核通过"} and record.get("final_decision")==INCLUDED: item["合理性分析初稿"]=record.get("analysis_draft") or initial_rationale(item)
            else: item["合理性分析初稿"]=""
            result_rows.append(item)
        results=pd.DataFrame(result_rows)
        st.session_state["review_results"]=results
        st.divider(); st.markdown("#### 导出最终结果")
        decision_records=[]
        for task in analyzed_map.values():
            decision_records.append(st.session_state.decisions.get((task["investee_company_name"],task["company_name"]),{}))
        ready=export_ready(decision_records)
        unfinished=sum(1 for record in decision_records if record.get("review_status") not in {REVIEWED,"复核通过"} or record.get("final_decision") not in {INCLUDED,EXCLUDED})
        included_count=sum(1 for record in decision_records if record.get("review_status") in {REVIEWED,"复核通过"} and record.get("final_decision")==INCLUDED)
        st.caption("最终 Excel 只输出人工确认“已复核 + 纳入”的记录，字段为被投企业、最终可比公司、证券代码和最终合理性分析。原工作簿不会被修改。")
        if ready: st.success(f"全部 {len(decision_records)} 条记录已完成复核和结论确认，其中 {included_count} 条将进入最终名单。")
        else: st.warning(f"还有 {unfinished} 条记录尚未完成复核或最终结论，完成前不能生成最终名单。")
        target_bytes=st.session_state.get("source_workbook_bytes"); target_name=st.session_state.get("source_workbook_name","")
        if target_bytes:
            st.info(f"当前输出底稿：{target_name}。系统直接复用第①步上传的工作簿并生成新副本，不需要再次上传，原文件不会被修改。")
            digest=hashlib.sha256(target_bytes).hexdigest()
            if st.button("生成最终 Excel 副本",type="primary",key="build_final_workbook",disabled=not ready):
                try:
                    filled,report=append_rationales_column(target_bytes,target_name,results)
                    st.session_state.final_workbook_result=(filled,report,target_name,digest)
                except Exception as e:
                    st.error(f"生成最终 Excel 失败：{e}")
            saved=st.session_state.get("final_workbook_result")
            if saved and saved[3]==digest:
                filled,report,source_name,_=saved
                st.dataframe(report,width="stretch",hide_index=True)
                completed=int(report["状态"].eq("已纳入").sum()) if not report.empty else 0
                if completed:
                    st.success(f"已生成 {completed} 条最终纳入记录。")
                else:
                    st.info("当前没有人工决定为“纳入”的记录，最终名单为空。")
                p=Path(source_name); out_name=f"{p.stem}_可比公司最终结果{p.suffix}"
                mime="application/vnd.ms-excel.sheet.macroEnabled.12" if p.suffix.lower()==".xlsm" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                st.download_button("下载最终 Excel 副本",filled,file_name=out_name,mime=mime,type="primary")
        else:
            st.warning("尚未取得原始工作簿，请返回第①步上传 Excel。")

elif page.startswith("⑤"):
    st.subheader("可比公司估值分析与结果导出")
    st.caption("系统直接复用第①步上传的数据和第④步人工确认的最终可比公司，不需要再次上传 Excel。所有估值方法先统一计算为被投企业股权价值，再形成低值、中值和高值区间。")
    analyzed_map=st.session_state.get("task_analysis",{})
    results=st.session_state.get("review_results",pd.DataFrame())
    if not analyzed_map or results.empty:
        st.info("请先完成第③步 AI 分析，并进入第④步生成人工复核任务。")
    else:
        decision_records=[st.session_state.decisions.get((task["investee_company_name"],task["company_name"]),{}) for task in analyzed_map.values()]
        ready=export_ready(decision_records)
        statuses=results.get("人工复核状态",pd.Series(index=results.index,dtype=object))
        decisions=results.get("人工最终结论",pd.Series(index=results.index,dtype=object))
        included=results.loc[statuses.isin([REVIEWED,"复核通过"]) & decisions.isin([INCLUDED,"最终纳入"])].copy()
        if not ready:
            unfinished=sum(1 for record in decision_records if record.get("review_status") not in {REVIEWED,"复核通过"} or record.get("final_decision") not in {INCLUDED,EXCLUDED})
            st.warning(f"还有 {unfinished} 条候选记录未完成复核或最终结论。估值应基于最终名单，请先返回第④步完成复核。")
        elif included.empty:
            st.warning("全部记录已复核，但当前没有最终纳入的可比公司，无法执行可比公司估值。")
        else:
            st.success(f"已读取 {included['investee_company_name'].nunique()} 家被投企业的 {len(included)} 条最终纳入可比关系。")
            input_fields=["company_name","market_cap","cash","interest_bearing_debt","minority_interest","preferred_stock","non_operating_investments","revenue","ebitda","ebitda_margin","net_profit","book_equity","total_assets","total_liabilities","reporting_period","valuation_date","currency","amount_unit"]
            labels={"company_name":"公司名称","market_cap":"市值","cash":"现金及现金等价物","interest_bearing_debt":"有息负债","minority_interest":"少数股东权益","preferred_stock":"优先股","non_operating_investments":"非经营性投资","revenue":"营业收入","ebitda":"EBITDA","ebitda_margin":"EBITDA利润率","net_profit":"净利润","book_equity":"账面净资产","total_assets":"总资产","total_liabilities":"总负债","reporting_period":"报告期","valuation_date":"估值基准日","currency":"币种","amount_unit":"金额单位"}
            numeric_fields=["market_cap","cash","interest_bearing_debt","minority_interest","preferred_stock","non_operating_investments","revenue","ebitda","ebitda_margin","net_profit","book_equity","total_assets","total_liabilities"]

            st.markdown("#### 估值数据检查与调整")
            st.info("蓝色表格中的修改仅用于本次估值和导出副本。企业价值必须使用有息负债；总负债只用于计算账面净资产，不能替代有息负债。")
            investee_source=tables["investee"].copy()
            for field in input_fields:
                if field not in investee_source: investee_source[field]=None
            investee_edit=st.data_editor(investee_source[input_fields].rename(columns=labels),width="stretch",hide_index=True,disabled=[labels["company_name"]],key="valuation_investee_editor")
            investee_adjusted=investee_edit.rename(columns={value:key for key,value in labels.items()})
            for field in numeric_fields:
                if field in investee_adjusted: investee_adjusted[field]=pd.to_numeric(investee_adjusted[field],errors="coerce")

            selected_peer_names=set(included["company_name"].astype(str))
            candidate_source=tables["candidates"].loc[tables["candidates"]["company_name"].astype(str).isin(selected_peer_names)].copy()
            for field in input_fields:
                if field not in candidate_source: candidate_source[field]=None
            with st.expander("检查或调整最终可比公司的估值数据",expanded=True):
                candidate_edit=st.data_editor(candidate_source[input_fields].rename(columns=labels),width="stretch",hide_index=True,disabled=[labels["company_name"]],key="valuation_candidate_editor")
            candidate_adjusted=candidate_edit.rename(columns={value:key for key,value in labels.items()})
            for field in numeric_fields:
                if field in candidate_adjusted: candidate_adjusted[field]=pd.to_numeric(candidate_adjusted[field],errors="coerce")

            missing_rows=[]
            for table_label,frame in [("被投企业",investee_adjusted),("可比公司",candidate_adjusted)]:
                for _,record in frame.iterrows():
                    required_values=[field for field in (["cash","interest_bearing_debt"] if table_label=="被投企业" else ["market_cap","cash","interest_bearing_debt"]) if pd.isna(record.get(field))]
                    if required_values:
                        missing_rows.append({"类型":table_label,"公司":record.get("company_name",""),"缺少关键字段":"、".join(labels[field] for field in required_values),"影响":"EV 类估值暂不可计算"})
            if missing_rows:
                st.warning("部分公司缺少企业价值或股权价值桥接数据。系统仍会计算其他可用方法。")
                st.dataframe(pd.DataFrame(missing_rows),width="stretch",hide_index=True)

            peer_detail=build_peer_valuation_detail(included,candidate_adjusted)
            stats=multiple_statistics(peer_detail) if not peer_detail.empty else pd.DataFrame()
            st.divider(); st.markdown("## 选择全局估值方案")
            st.caption("先选择估值方法，再选择倍数。高级输入默认收起，只有点击“添加方法”或“自定义”时才展开。")
            st.markdown("""
            <style>
            div[data-testid="stVerticalBlockBorderWrapper"]:has(.valuation-card-marker) {
                border: 1px solid #d9dfcf;
                border-radius: 16px;
                background: #ffffff;
                padding: 0.35rem 0.55rem 0.55rem;
                box-shadow: none;
            }
            div[data-testid="stVerticalBlockBorderWrapper"]:has(.valuation-card-marker) .stMultiSelect span[data-baseweb="tag"] {
                background-color: #eaf2cf !important;
                border: 1px solid #98b331 !important;
                color: #47611e !important;
                font-weight: 700;
            }
            div[data-testid="stVerticalBlockBorderWrapper"]:has(.valuation-card-marker) button[kind="secondary"] {
                border-color: #d1d8c7;
                background: #ffffff;
                color: #2f3a2b;
                font-weight: 600;
            }
            div[data-testid="stVerticalBlockBorderWrapper"]:has(.valuation-card-marker) button[kind="secondary"]:hover {
                border-color: #91ac30;
                color: #607b22;
            }
            .valuation-card-marker { height: 0; overflow: hidden; }
            .valuation-card-title {
                display: flex;
                align-items: center;
                gap: 0.75rem;
                margin: 0.1rem 0 0.1rem;
            }
            .valuation-card-number {
                width: 2rem;
                height: 2rem;
                border-radius: 999px;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                background: #95ae2d;
                color: white;
                font-weight: 800;
                flex: 0 0 auto;
            }
            .valuation-card-name {
                color: #252a37;
                font-size: 1.25rem;
                font-weight: 750;
            }
            .valuation-card-help {
                color: #7b8278;
                margin: 0 0 0.75rem 2.75rem;
                line-height: 1.55;
            }
            </style>
            """,unsafe_allow_html=True)

            custom_methods={}
            extended_stats=stats.copy()

            with st.container(border=True):
                st.markdown('<div class="valuation-card-marker"></div><div class="valuation-card-title"><span class="valuation-card-number">1</span><span class="valuation-card-name">估值方法</span></div><div class="valuation-card-help">选择本次估值采用的方法，默认 EV/EBITDA；支持多选。</div>',unsafe_allow_html=True)
                method_select_col,method_action_col,method_hint_col=st.columns([3.3,0.9,2.2],vertical_alignment="bottom")
                with method_action_col:
                    with st.popover("＋ 添加方法",use_container_width=True):
                        st.markdown("#### 添加自定义估值方法")
                        use_custom_method=st.checkbox("启用此自定义方法",key="global_custom_method")
                        numerator_label=st.selectbox("分子",["企业价值（EV）","股权价值（市值）"],disabled=not use_custom_method,key="global_custom_numerator")
                        denominator_labels={"revenue":"营业收入","ebitda":"EBITDA","net_profit":"净利润","book_equity":"账面净资产","total_assets":"总资产"}
                        denominator_field=st.selectbox("分母",list(denominator_labels),format_func=lambda value:denominator_labels[value],disabled=not use_custom_method,key="global_custom_denominator")
                        basis_code="EV" if numerator_label.startswith("企业价值") else "Equity"
                        numerator_column="企业价值" if basis_code=="EV" else "市值"
                        denominator_column={"revenue":"营业收入","ebitda":"EBITDA","net_profit":"净利润","book_equity":"账面净资产","total_assets":"总资产"}[denominator_field]
                        generated_name=f"{'EV' if basis_code=='EV' else '股权价值'}/{denominator_labels[denominator_field]}"
                        custom_name=text_value(st.text_input("方法名称（可选）",placeholder=generated_name,disabled=not use_custom_method,key="global_custom_method_name")) or generated_name
                        if use_custom_method:
                            if custom_name in ["EV/Revenue","EV/EBITDA","P/E","P/B"]:
                                st.warning("名称与预设方法重复，请修改名称。")
                            else:
                                peer_detail[custom_name]=(pd.to_numeric(peer_detail[numerator_column],errors="coerce")/pd.to_numeric(peer_detail[denominator_column],errors="coerce")).replace([float("inf"),float("-inf")],pd.NA)
                                custom_stat_rows=[]
                                for investee_name,group in peer_detail.groupby("被投企业",sort=False):
                                    values=pd.to_numeric(group[custom_name],errors="coerce").dropna(); values=values[values>0]
                                    if not values.empty:
                                        custom_stat_rows.append({"被投企业":investee_name,"估值方法":custom_name,"有效样本数":int(len(values)),"最小值":float(values.min()),"P25":float(values.quantile(.25)),"中位数":float(values.median()),"P75":float(values.quantile(.75)),"最大值":float(values.max()),"平均值":float(values.mean())})
                                if custom_stat_rows:
                                    extended_stats=pd.concat([extended_stats,pd.DataFrame(custom_stat_rows)],ignore_index=True)
                                    custom_methods[custom_name]={"metric":denominator_field,"basis":basis_code,"formula":f"{numerator_column} / {denominator_column}"}
                                    st.success(f"已启用：{custom_name}")
                                else:
                                    st.warning("当前可比公司缺少有效分子或分母数据。")
                with method_hint_col:
                    st.caption("点击“添加方法”后，再填写分子与分母。")
                method_options=[method for method in ["EV/EBITDA","EV/Revenue","P/E","P/B",*custom_methods] if method in extended_stats.get("估值方法",pd.Series(dtype=str)).astype(str).unique()]
                global_method_signature=hashlib.sha1("|".join(method_options).encode("utf-8")).hexdigest()[:10]
                with method_select_col:
                    selected_methods=st.multiselect("已选方法",method_options,default=["EV/EBITDA"] if "EV/EBITDA" in method_options else method_options[:1],key=f"global_valuation_methods_{global_method_signature}",label_visibility="collapsed")

            def parse_number_list(raw,minimum=None,maximum=None):
                parsed=[]
                for token in str(raw).replace("，",",").split(","):
                    try: value=float(token.strip())
                    except (TypeError,ValueError): continue
                    if (minimum is None or value>=minimum) and (maximum is None or value<=maximum) and value not in parsed: parsed.append(value)
                return parsed

            with st.container(border=True):
                st.markdown('<div class="valuation-card-marker"></div><div class="valuation-card-title"><span class="valuation-card-number">2</span><span class="valuation-card-name">倍数选择</span></div><div class="valuation-card-help">默认使用 P25、P50、P75；可以删除任意默认项，自定义内容不会直接铺在页面上。</div>',unsafe_allow_html=True)
                source_select_col,source_action_col,source_hint_col=st.columns([3.3,0.9,2.2],vertical_alignment="bottom")
                with source_action_col:
                    with st.popover("＋ 自定义",use_container_width=True):
                        st.markdown("#### 添加自定义倍数")
                        percentile_tab,multiple_tab=st.tabs(["自定义分位点","手工倍数"])
                        with percentile_tab:
                            custom_percentile_text=st.text_input("分位点",placeholder="例如：10,40,90",key="custom_percentile_text",help="输入0至100之间的数字，用英文或中文逗号分隔。")
                        with multiple_tab:
                            manual_multiple_text=st.text_input("倍数",placeholder="例如：8,10,12",key="manual_multiple_text",help="直接输入一个或多个采用倍数，用英文或中文逗号分隔。")
                with source_hint_col:
                    st.caption("点击后添加自定义分位点或手工倍数。")
                with source_select_col:
                    selected_sources=st.multiselect("默认倍数",["P25","P50","P75"],default=["P25","P50","P75"],key="global_multiple_sources",label_visibility="collapsed")
                custom_percentiles=parse_number_list(custom_percentile_text,0,100)
                manual_multiples=parse_number_list(manual_multiple_text,0,None)
                custom_labels=[*[f"P{value:g}" for value in custom_percentiles if f"P{value:g}" not in selected_sources],*[f"手工{value:g}x" for value in manual_multiples]]
                if custom_labels: st.caption("已添加自定义倍数："+"、".join(custom_labels))

            with st.container(border=True):
                st.markdown('<div class="valuation-card-marker"></div><div class="valuation-card-title"><span class="valuation-card-number">3</span><span class="valuation-card-name">估值调整项</span></div><div class="valuation-card-help">控制权溢价、流动性折价及其他调整单独管理，与估值方法和倍数分开。</div>',unsafe_allow_html=True)
                with st.expander("展开设置",expanded=False):
                    st.caption("默认提供控制权溢价和流动性折价；可删除默认项，也可新增固定金额或百分比调整。")
                    default_adjustments=pd.DataFrame([
                        {"调整项名称":"控制权溢价","调整阶段":"股权价值","调整类型":"百分比","运算方式":"加","数值":0.0},
                        {"调整项名称":"流动性折价","调整阶段":"股权价值","调整类型":"百分比","运算方式":"减","数值":0.0},
                    ])
                    adjustments=st.data_editor(default_adjustments,num_rows="dynamic",width="stretch",hide_index=True,key="valuation_adjustments",column_config={
                        "调整项名称":st.column_config.TextColumn("调整项名称",required=True),
                        "调整阶段":st.column_config.SelectboxColumn("调整阶段",options=["企业价值","股权价值"],required=True),
                        "调整类型":st.column_config.SelectboxColumn("调整类型",options=["百分比","固定金额"],required=True),
                        "运算方式":st.column_config.SelectboxColumn("运算方式",options=["加","减"],required=True),
                        "数值":st.column_config.NumberColumn("数值",step=0.01,format="%.2f",required=True),
                    })

            valuation_scheme_ready=bool(selected_methods and (selected_sources or custom_percentiles or manual_multiples))
            if not valuation_scheme_ready:
                st.warning("请至少选择一种估值方法和一个倍数。当前不会计算估值，Excel导出也将停用。")

            def plan_for_target(target_stats):
                plan=[]
                source_map={"P25":"P25","P50":"中位数","P75":"P75"}
                for method in selected_methods:
                    match=target_stats.loc[target_stats["估值方法"].astype(str).eq(method)]
                    if match.empty: continue
                    stat=match.iloc[0]
                    for source in selected_sources: plan.append({"估值方法":method,"情景":source,"采用倍数":float(stat[source_map[source]])})
                    values=peer_detail.loc[peer_detail["被投企业"].astype(str).eq(str(stat["被投企业"])),method] if method in peer_detail else pd.Series(dtype=float)
                    if method in custom_methods:
                        group=peer_detail.loc[peer_detail["被投企业"].astype(str).eq(str(stat["被投企业"]))]
                        definition=custom_methods[method]; numerator_col="企业价值" if definition["basis"]=="EV" else "市值"
                        denominator_col={"revenue":"营业收入","ebitda":"EBITDA","net_profit":"净利润","book_equity":"账面净资产","total_assets":"总资产"}[definition["metric"]]
                        values=pd.to_numeric(group[numerator_col],errors="coerce")/pd.to_numeric(group[denominator_col],errors="coerce")
                    values=pd.to_numeric(values,errors="coerce").dropna(); values=values[values>0]
                    for percentile in custom_percentiles:
                        scenario=f"P{percentile:g}"
                        if scenario not in selected_sources and not values.empty: plan.append({"估值方法":method,"情景":scenario,"采用倍数":float(values.quantile(percentile/100))})
                    for multiple in manual_multiples: plan.append({"估值方法":method,"情景":f"手工{multiple:g}x","采用倍数":multiple})
                return pd.DataFrame(plan,columns=["估值方法","情景","采用倍数"])

            all_plan_rows=[]; all_plan_settings=[]
            for _,investee_row in investee_adjusted.iterrows():
                investee_name=str(investee_row.get("company_name","")); target_stats=extended_stats.loc[extended_stats["被投企业"].astype(str).eq(investee_name)]
                target_plan=plan_for_target(target_stats)
                if not target_plan.empty: all_plan_settings.append(target_plan.assign(被投企业=investee_name)[["被投企业","估值方法","情景","采用倍数"]])
                detail=apply_valuation_plan(investee_row.to_dict(),target_plan,custom_methods=custom_methods,adjustments=adjustments)
                if not detail.empty: all_plan_rows.append(detail)
            calculation_detail=pd.concat(all_plan_rows,ignore_index=True) if all_plan_rows else pd.DataFrame()
            valuation_plan_export=pd.concat(all_plan_settings,ignore_index=True) if all_plan_settings else pd.DataFrame()

            st.divider(); st.markdown("## 二、所有企业估值总览")
            st.caption("一行对应一家被投企业。点击任意一行，可在下方查看该企业的基础数据、可比公司、倍数和完整计算过程。")
            overview_rows=[]
            for _,investee_row in investee_adjusted.iterrows():
                name=str(investee_row.get("company_name","")); target_stats=extended_stats.loc[extended_stats["被投企业"].astype(str).eq(name)]
                target_detail=calculation_detail.loc[calculation_detail["被投企业"].astype(str).eq(name)] if not calculation_detail.empty else pd.DataFrame()
                counts=[]; prompts=[]
                for method in selected_methods:
                    match=target_stats.loc[target_stats["估值方法"].astype(str).eq(method)]
                    if match.empty:
                        counts.append(f"{method}：0家"); prompts.append(f"{method}无有效可比倍数")
                    else:
                        count=int(match.iloc[0]["有效样本数"]); counts.append(f"{method}：{count}家")
                        if count<3: prompts.append(f"{method}有效样本少于3家")
                        if target_detail.loc[target_detail["估值方法"].astype(str).eq(method),"最终股权价值"].notna().sum()==0: prompts.append(f"{method}缺少被投企业指标或EV桥接数据")
                result={"被投企业":name,"估值方法":"、".join(selected_methods) or "未选择","有效可比公司数":"；".join(counts) or "0家"}
                for source in ["P25","P50","P75"]:
                    source_values=pd.to_numeric(target_detail.loc[target_detail["情景"].eq(source),"最终股权价值"],errors="coerce").dropna() if not target_detail.empty else pd.Series(dtype=float)
                    result[f"{source}股权价值"]=float(source_values.mean()) if not source_values.empty else None
                all_values=pd.to_numeric(target_detail.get("最终股权价值"),errors="coerce").dropna() if not target_detail.empty else pd.Series(dtype=float)
                result["方案最低值"]=float(all_values.min()) if not all_values.empty else None
                result["方案最高值"]=float(all_values.max()) if not all_values.empty else None
                result["计算提示"]="；".join(dict.fromkeys(prompts)) or "无"
                overview_rows.append(result)
            overview=pd.DataFrame(overview_rows)
            overview_filter_cols=st.columns([3,1.2,1.2])
            overview_search=overview_filter_cols[0].text_input("搜索被投企业",placeholder="输入企业名称",key="valuation_overview_search")
            overview_page_size=overview_filter_cols[1].selectbox("每页显示",[20,50,100,200],key="valuation_overview_page_size")
            overview_visible=overview
            if overview_search.strip(): overview_visible=overview_visible.loc[overview_visible["被投企业"].astype(str).str.contains(overview_search.strip(),case=False,regex=False)]
            overview_page_count=max(1,(len(overview_visible)+overview_page_size-1)//overview_page_size)
            if not 1<=int(st.session_state.get("valuation_overview_page",1))<=overview_page_count: st.session_state["valuation_overview_page"]=1
            overview_page=overview_filter_cols[2].number_input("页码",1,overview_page_count,1,key="valuation_overview_page")
            overview_visible=overview_visible.iloc[(overview_page-1)*overview_page_size:overview_page*overview_page_size].reset_index(drop=True)
            overview_event=st.dataframe(overview_visible.round(2),width="stretch",hide_index=True,on_select="rerun",selection_mode="single-row",key="valuation_overview_table")
            if overview_event.selection.rows:
                st.session_state["valuation_detail_target"]=str(overview_visible.iloc[overview_event.selection.rows[0]]["被投企业"])
            selected_target=st.session_state.get("valuation_detail_target")
            if selected_target not in overview["被投企业"].astype(str).tolist(): selected_target=None
            if not selected_target and not overview.empty: selected_target=str(overview.iloc[0]["被投企业"])

            st.divider(); st.markdown("## 三、单家企业估值明细")
            st.caption("默认展示总览中的第一家企业；点击总览中的其他行即可切换。")
            st.markdown(f"### {selected_target}")

            st.markdown("#### 第一步：确认被投企业基础数据")
            st.caption("这些数据来自第一次上传的 investee 工作表；上方人工调整后，以下计算会立即更新。")
            target_base=investee_adjusted.loc[investee_adjusted["company_name"].astype(str).eq(selected_target)].copy()
            target_base_view=target_base[[c for c in ["company_name","revenue","ebitda","net_profit","book_equity","cash","interest_bearing_debt","minority_interest","preferred_stock","non_operating_investments","reporting_period","valuation_date","currency","amount_unit"] if c in target_base]].rename(columns=labels)
            st.dataframe(target_base_view,width="stretch",hide_index=True)

            st.markdown("#### 第二步：计算最终可比公司的企业价值")
            st.code("企业价值（EV）= 市值 + 有息负债 + 少数股东权益 + 优先股 − 现金及现金等价物 − 非经营性投资",language=None)
            selected_peers=peer_detail.loc[peer_detail["被投企业"].astype(str).eq(selected_target)].copy()
            ev_columns=["可比公司","证券代码","市值","有息负债","少数股东权益","优先股","现金","非经营性投资","企业价值"]
            if selected_peers.empty:
                st.warning("该被投企业没有可用于估值的最终可比公司数据。")
            else:
                st.dataframe(selected_peers[[c for c in ev_columns if c in selected_peers]],width="stretch",hide_index=True)

            st.markdown("#### 第三步：计算每家可比公司的估值倍数")
            st.code("EV/Revenue = 企业价值 ÷ 营业收入\nEV/EBITDA = 企业价值 ÷ EBITDA\nP/E = 市值 ÷ 净利润\nP/B = 市值 ÷ 账面净资产",language=None)
            multiple_columns=["可比公司","营业收入","EBITDA","净利润","账面净资产",*selected_methods]
            if not selected_peers.empty:
                st.dataframe(selected_peers[[c for c in multiple_columns if c in selected_peers]].round(2),width="stretch",hide_index=True)

            st.markdown("#### 第四步：统计可比公司倍数区间")
            st.caption("显示全局方案所用方法的倍数分布。P50即中位数；有效样本少于3家时会明确提示。")
            selected_stats=extended_stats.loc[extended_stats["被投企业"].astype(str).eq(selected_target) & extended_stats["估值方法"].astype(str).isin(selected_methods)].copy() if not extended_stats.empty else pd.DataFrame()
            if selected_stats.empty:
                st.warning("当前没有可以统计的有效估值倍数。")
            else:
                st.dataframe(selected_stats.round(2),width="stretch",hide_index=True)
                if (selected_stats["有效样本数"]<3).any(): st.warning("该被投企业部分估值方法的有效可比公司少于 3 家，区间结果仅供演示和人工判断。")

            st.markdown("#### 第五步：将倍数应用到被投企业并换算股权价值")
            st.code("被投企业指标 × 采用倍数 = 隐含企业价值或股权价值\nEV类方法：隐含企业价值 ± 企业价值调整 − 有息负债 − 少数股东权益 − 优先股 + 现金 + 非经营性投资\n股权价值类方法：直接得到调整前股权价值\n调整前股权价值 ± 已配置的股权价值调整 = 最终股权价值",language=None)
            selected_calculation=calculation_detail.loc[calculation_detail["被投企业"].astype(str).eq(selected_target)].copy() if not calculation_detail.empty else pd.DataFrame()
            if selected_calculation.empty:
                st.warning("当前数据不足以形成股权价值结果。请补充上方缺失字段，或确认至少一种估值方法具备有效数据。")
            else:
                plan_signature=hashlib.sha1((selected_target+"|"+"|".join(selected_calculation["情景"].astype(str))).encode("utf-8")).hexdigest()[:12]
                filter_method_col,filter_scenario_col=st.columns(2)
                calculation_methods=selected_calculation["估值方法"].drop_duplicates().astype(str).tolist()
                calculation_scenarios=selected_calculation["情景"].drop_duplicates().astype(str).tolist()
                shown_methods=filter_method_col.multiselect("筛选表格中的估值方法",calculation_methods,default=calculation_methods,key=f"valuation_table_methods_{plan_signature}")
                shown_scenarios=filter_scenario_col.multiselect("筛选表格中的倍数来源",calculation_scenarios,default=calculation_scenarios,key=f"valuation_table_scenarios_{plan_signature}")
                displayed_calculation=selected_calculation.loc[selected_calculation["估值方法"].isin(shown_methods) & selected_calculation["情景"].isin(shown_scenarios)]
                st.dataframe(displayed_calculation.round(2),width="stretch",hide_index=True)

            st.markdown("#### 第六步：形成选定方案的估值结果")
            st.caption("当前对选中的有效估值方法进行等权平均；上方方法、情景或倍数变化后，结果会同步更新。")
            if not selected_calculation.empty and pd.to_numeric(selected_calculation["最终股权价值"],errors="coerce").notna().any():
                plan_summary=(selected_calculation.assign(_value=pd.to_numeric(selected_calculation["最终股权价值"],errors="coerce"))
                              .dropna(subset=["_value"]).groupby(["被投企业","情景"],as_index=False)
                              .agg(采用方法数=("估值方法","nunique"),方案股权价值=("_value","mean")))
                plan_summary["汇总方式"]="同一倍数来源下，所选有效方法等权平均"
                plan_summary=plan_summary.rename(columns={"情景":"倍数来源"})[["被投企业","倍数来源","采用方法数","汇总方式","方案股权价值"]]
                st.dataframe(plan_summary.round(2),width="stretch",hide_index=True)
            else:
                plan_summary=pd.DataFrame()
                st.warning("当前估值方案没有形成有效股权价值，请检查所选方法、倍数和基础数据。")
            source_bytes=st.session_state.get("source_workbook_bytes")
            source_name=st.session_state.get("source_workbook_name","")
            st.divider(); st.markdown("#### 导出完整估值工作簿")
            st.caption("输出副本保留原始工作表，并新增估值方案、调整项、全部企业估值汇总及逐项计算过程。")
            export_disabled=calculation_detail.empty or not source_bytes
            export_signature=hashlib.sha256((str(source_name)+included.to_json()+investee_adjusted.to_json()+candidate_adjusted.to_json()+calculation_detail.to_json()+adjustments.to_json()).encode("utf-8")).hexdigest()
            if st.button("生成完整估值 Excel 副本",type="primary",disabled=export_disabled,key="build_valuation_workbook"):
                try:
                    adjustment_export=pd.concat([investee_adjusted.assign(数据类型="被投企业"),candidate_adjusted.assign(数据类型="可比公司")],ignore_index=True)
                    sheets={"估值数据调整":adjustment_export,"估值方案":valuation_plan_export,"估值调整项":adjustments,"可比公司估值明细":peer_detail,"估值倍数统计":extended_stats,"估值计算过程":calculation_detail,"估值汇总":overview}
                    filled,report=append_valuation_sheets(source_bytes,source_name,results,sheets)
                    st.session_state.valuation_export_result=(filled,report,source_name,export_signature)
                except Exception as exc:
                    st.error(f"生成估值 Excel 失败：{exc}")
            saved=st.session_state.get("valuation_export_result")
            if saved and saved[3]==export_signature:
                filled,report,original_name,_=saved
                p=Path(original_name); out_name=f"{p.stem}_可比公司估值结果{p.suffix}"
                mime="application/vnd.ms-excel.sheet.macroEnabled.12" if p.suffix.lower()==".xlsm" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                st.success("完整估值工作簿已经生成。")
                st.download_button("下载完整估值 Excel",filled,file_name=out_name,mime=mime,type="primary")

st.markdown('<div class="workflow-footer"></div>',unsafe_allow_html=True)
prev_col,tip_col,next_col=st.columns([1,5,1])
with prev_col:
    st.button("← 上一步",key="workflow_prev",disabled=current_step==0,on_click=go_to_step,args=(-1,))
with tip_col:
    st.caption(f"流程步骤 {current_step+1}/{len(STEPS)} · 完成当前页面后继续下一步；也可从左侧直接切换。")
with next_col:
    st.button("下一步 →",key="workflow_next",type="primary",disabled=current_step==len(STEPS)-1,on_click=go_to_step,args=(1,))
