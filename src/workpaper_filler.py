"""Write reviewed rationales to a downloadable copy of a supplied Excel workpaper."""
from io import BytesIO
from pathlib import PureWindowsPath, PurePosixPath
import re
import pandas as pd
from openpyxl import load_workbook
from copy import copy
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

def _basename(value: str) -> str:
    value=str(value or "").strip()
    return PureWindowsPath(value.replace("/", "\\")).name or PurePosixPath(value).name

def fill_workpaper(workbook_bytes: bytes, filename: str, reviewed: pd.DataFrame, overwrite: bool=False) -> tuple[bytes, pd.DataFrame]:
    """Fill inclusion rationales in a copy, using reviewed rows' workbook/sheet/cell mapping.

    Only rows explicitly marked 最终纳入 are considered. Duplicate target cells are
    rejected as a group to prevent last-write-wins data loss.
    """
    suffix=filename.lower().endswith(".xlsm")
    wb=load_workbook(BytesIO(workbook_bytes), keep_vba=suffix)
    target_name=_basename(filename).casefold()
    decisions=reviewed.get("人工最终决定",pd.Series(index=reviewed.index,dtype=object))
    rows=reviewed.loc[decisions.eq("最终纳入")].copy()
    reports=[]; valid=[]; seen={}
    for idx,row in reviewed.loc[~decisions.eq("最终纳入")].iterrows():
        reports.append({"被投企业":row.get("investee_company_name",""),"候选公司":row.get("company_name",""),"工作表":row.get("sheet_name",""),"单元格":row.get("cell_address",""),"状态":"未写入","说明":f"人工最终决定为“{row.get('人工最终决定','未填写')}”；仅最终纳入的记录会写入合理性分析。"})
    for idx,row in rows.iterrows():
        company=str(row.get("company_name", "")); investee=str(row.get("investee_company_name", ""))
        mapped=_basename(row.get("workpaper_file", ""))
        sheet=str(row.get("sheet_name", "") or "").strip(); cell=str(row.get("cell_address", "") or "").strip().upper()
        status="待处理"; detail=""
        if not row.get("合理性分析初稿"):
            status,detail="跳过","没有可写入的合理性分析初稿"
        elif not mapped or not sheet or not cell:
            status,detail="跳过","缺少 workpaper_file、sheet_name 或 cell_address"
        elif mapped.casefold()!=target_name:
            status,detail="跳过",f"映射文件名与上传文件 {filename} 不一致"
        elif sheet not in wb.sheetnames:
            status,detail="跳过",f"工作表不存在：{sheet}"
        elif not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]*",cell):
            status,detail="跳过",f"单元格地址无效：{cell}"
        else:
            key=(sheet,cell); seen.setdefault(key,[]).append((idx,company,investee,row))
            valid.append((idx,company,investee,sheet,cell,row))
            continue
        reports.append({"被投企业":investee,"候选公司":company,"工作表":sheet,"单元格":cell,"状态":status,"说明":detail})
    duplicate_ids={item[0] for cell_rows in seen.values() if len(cell_rows)>1 for item in cell_rows}
    for idx,company,investee,sheet,cell,row in valid:
        key=(sheet,cell); existing=wb[sheet][cell].value
        if idx in duplicate_ids:
            status,detail="跳过",f"目标位置重复：{sheet}!{cell}；请为每条分析指定唯一单元格"
        elif existing not in (None, "") and not overwrite:
            status,detail="跳过",f"目标单元格已有内容：{str(existing)[:80]}；勾选覆盖选项后重试"
        else:
            wb[sheet][cell]=str(row["合理性分析初稿"])
            status,detail="已写入","结果写入输出副本，原始上传文件未修改"
        reports.append({"被投企业":investee,"候选公司":company,"工作表":sheet,"单元格":cell,"状态":status,"说明":detail})
    if reviewed.empty:
        reports.append({"被投企业":"","候选公司":"","工作表":"","单元格":"","状态":"未写入","说明":"当前没有可回填的复核记录。"})
    output=BytesIO(); wb.save(output)
    return output.getvalue(),pd.DataFrame(reports,columns=["被投企业","候选公司","工作表","单元格","状态","说明"])

def append_rationales_column(workbook_bytes: bytes, filename: str, reviewed: pd.DataFrame, overwrite: bool=True) -> tuple[bytes, pd.DataFrame]:
    """Create a clean final comparable-company list in a copy of the workbook.

    Only rows explicitly marked ``已复核`` and ``纳入`` are exported.  The source
    workbook and its existing sheets remain untouched; the generated copy contains one
    dedicated sheet with the four fields required for the final deliverable.
    """
    wb=load_workbook(BytesIO(workbook_bytes),keep_vba=filename.lower().endswith(".xlsm"))
    statuses=reviewed.get("人工复核状态",pd.Series(index=reviewed.index,dtype=object))
    decisions=reviewed.get("人工最终结论",reviewed.get("人工最终决定",pd.Series(index=reviewed.index,dtype=object)))
    included=reviewed.loc[statuses.isin(["已复核","复核通过"]) & decisions.isin(["纳入","最终纳入"])].copy()

    title="最终可比公司名单"
    if title in wb.sheetnames:
        del wb[title]
    ws=wb.create_sheet(title)
    headers=["被投企业","最终可比公司","证券代码","最终合理性分析"]
    ws.append(headers)
    reports=[]
    for _,row in included.iterrows():
        investee=str(row.get("investee_company_name","") or "").strip()
        company=str(row.get("company_name","") or "").strip()
        stock_code=str(row.get("stock_code","") or "").strip()
        reason=str(row.get("合理性分析初稿","") or "").strip()
        if not investee or not company or not reason:
            reports.append({"工作表":title,"行号":"","被投企业":investee,"候选公司":company,"状态":"跳过","说明":"缺少企业名称或最终合理性分析。"})
            continue
        ws.append([investee,company,stock_code,reason])
        row_no=ws.max_row
        alignment=copy(ws.cell(row_no,4).alignment)
        alignment.wrap_text=True; alignment.vertical="top"
        ws.cell(row_no,4).alignment=alignment
        reports.append({"工作表":title,"行号":row_no,"被投企业":investee,"候选公司":company,"状态":"已纳入","说明":"已写入最终可比公司名单。"})
    ws.freeze_panes="A2"
    ws.auto_filter.ref=f"A1:D{max(1,ws.max_row)}"
    for column,width in {"A":24,"B":24,"C":16,"D":80}.items():
        ws.column_dimensions[column].width=width
    if not reports:
        reports.append({"工作表":title,"行号":"","被投企业":"","候选公司":"","状态":"未写入","说明":"当前没有同时满足“已复核”和“纳入”的记录。"})
    wb.active=wb.worksheets.index(ws)
    output=BytesIO(); wb.save(output)
    columns=["工作表","行号","被投企业","候选公司","状态","说明"]
    return output.getvalue(),pd.DataFrame(reports,columns=columns)


def append_valuation_sheets(workbook_bytes: bytes, filename: str, reviewed: pd.DataFrame,
                            valuation_sheets: dict[str, pd.DataFrame]) -> tuple[bytes, pd.DataFrame]:
    """Append the final list and valuation schedules to one copy of the source workbook."""
    final_bytes, report = append_rationales_column(workbook_bytes, filename, reviewed)
    keep_vba = filename.lower().endswith(".xlsm")
    wb = load_workbook(BytesIO(final_bytes), keep_vba=keep_vba)
    for title, frame in valuation_sheets.items():
        title = str(title)[:31]
        if title in wb.sheetnames:
            del wb[title]
        ws = wb.create_sheet(title)
        if frame is None or frame.empty:
            ws.append(["说明"]); ws.append(["当前没有可计算的数据。"])
        else:
            ws.append(list(frame.columns))
            for row in frame.itertuples(index=False, name=None):
                ws.append([None if pd.isna(value) else value for value in row])
        for cell in ws[1]:
            cell.font=Font(bold=True,color="FFFFFF")
            cell.fill=PatternFill("solid",fgColor="183B56")
            cell.alignment=Alignment(wrap_text=True,vertical="center")
        ws.freeze_panes="A2"
        ws.auto_filter.ref=ws.dimensions
        for idx,column in enumerate(ws.columns,1):
            sample=list(column)[:100]
            width=min(42,max(12,max((len(str(cell.value or "")) for cell in sample),default=10)+2))
            ws.column_dimensions[get_column_letter(idx)].width=width
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value,float): cell.number_format='#,##0.00;[Red](#,##0.00);-'
                alignment=copy(cell.alignment); alignment.vertical="top"; cell.alignment=alignment
    output=BytesIO(); wb.save(output)
    return output.getvalue(), report
