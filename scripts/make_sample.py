from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
def build():
    investees=[
      {"company_id":"DEMO-001","company_name":"云帆运营软件（虚构）","industry":"企业软件","company_description":"为中小企业提供云端运营管理工具。","main_business":"开发订阅制云端运营与流程管理软件。","products":"云端运营管理平台、流程自动化模块","application_scenarios":"订单管理、库存协同、门店运营","customer_groups":"中小型零售与服务企业","business_model":"SaaS订阅及实施服务","revenue":120,"ebitda":14.4,"net_profit":9.6,"total_assets":180,"total_liabilities":75.6,"book_equity":104.4,"cash":28,"interest_bearing_debt":42,"minority_interest":0,"preferred_stock":0,"non_operating_investments":4,"debt_to_assets":.42,"revenue_growth":.24,"gross_margin":.68,"ebitda_margin":.12,"reporting_period":"2025年度","valuation_date":"2025-12-31","currency":"CNY","amount_unit":"百万元"},
      {"company_id":"DEMO-002","company_name":"青禾环保包装（虚构）","industry":"包装材料","company_description":"生产可降解食品包装材料。","main_business":"研发和销售植物纤维环保包装及相关材料。","products":"食品餐盒、纸浆模塑包装、定制包装","application_scenarios":"餐饮外卖、食品加工、零售包装","customer_groups":"餐饮连锁、食品加工企业、零售品牌","business_model":"产品销售与定制加工","revenue":260,"ebitda":28.6,"net_profit":18.2,"total_assets":310,"total_liabilities":179.8,"book_equity":130.2,"cash":36,"interest_bearing_debt":112,"minority_interest":3,"preferred_stock":0,"non_operating_investments":6,"debt_to_assets":.58,"revenue_growth":.16,"gross_margin":.31,"ebitda_margin":.11,"reporting_period":"2025年度","valuation_date":"2025-12-31","currency":"CNY","amount_unit":"百万元"},
    ]
    specs=[
      ("企业软件","云端运营管理软件","订阅制SaaS软件","中小企业客户","运营平台流程模块"),
      ("包装材料","环保食品包装产品","材料销售定制加工","餐饮与食品企业","食品包装餐盒模塑"),
    ]
    close_variants=[
      [
        ("面向连锁零售的云端门店运营软件。","销售标准化门店运营SaaS。","门店运营平台、库存协同模块","连锁门店、库存与订单协同","中小型连锁零售企业","按门店数量订阅收费"),
        ("面向制造企业的云端流程协同软件。","提供采购、生产与库存流程管理软件。","生产协同平台、采购管理模块","制造排产、采购及库存管理","中型制造企业","SaaS订阅及实施服务"),
        ("提供企业流程自动化和低代码工具。","研发流程自动化与低代码应用平台。","流程引擎、低代码开发平台","审批流程及内部应用开发","成长型企业及IT部门","平台订阅与增值模块收费"),
        ("面向服务行业的客户与订单管理软件。","销售客户管理、订单履约和数据分析软件。","客户管理系统、订单分析模块","客户运营、订单履约与经营分析","专业服务及消费服务企业","软件订阅与顾问实施"),
        ("提供中小企业一体化经营管理软件。","研发进销存、财务及门店管理SaaS。","进销存系统、财务与门店模块","零售门店经营及财务管理","小微零售与批发企业","标准化SaaS订阅"),
      ],
      [
        ("生产植物纤维一次性食品餐盒。","研发和销售纸浆模塑餐盒及配套餐饮包装。","甘蔗浆餐盒、纸浆模塑托盘","连锁餐饮、外卖配送","连锁餐饮企业与外卖平台","标准产品销售"),
        ("生产食品加工用纸浆模塑内托。","提供食品内托、防护包装及定制开模服务。","食品内托、防护托盘","食品加工、冷链运输","食品加工企业","定制加工与模具服务"),
        ("研发可降解薄膜及软包装材料。","销售生物基薄膜和食品软包装。","可降解薄膜、食品包装袋","烘焙食品、零售商品包装","食品品牌与包装转换商","材料销售及联合开发"),
        ("提供纸质零售包装和品牌定制服务。","设计生产纸盒、纸袋及品牌展示包装。","零售纸盒、手提袋、展示包装","品牌零售、礼赠场景","消费品牌与零售商","项目制设计与批量生产"),
        ("生产环保食品包装和外卖餐具。","销售餐盒、杯具及可降解配套餐具。","外卖餐盒、杯具、可降解餐具","餐饮外卖、便利零售","餐饮连锁与经销商","标准产品销售及经销"),
      ],
    ]
    rows=[]
    for group,(industry,biz,model,customers,product) in enumerate(specs):
      for i in range(12):
        close=i<5
        variant=close_variants[group][i] if close else None
        assets=round(investees[group]["total_assets"]*([.5,.8,1.1,1.6,2.5,4,.15,1.4,.9,1.2,5,.6][i]),2); leverage=[.4,.38,.45,.5,.56,.62,.35,.48,.52,.43,.7,.46][i] if group==0 else [.55,.52,.58,.61,.66,.72,.48,.57,.63,.54,.78,.59][i]
        ebitda_margin=[.13,.1,.15,.08,.12,.04,.18,.11,.09,.16,.02,.1][i] if group==0 else [.1,.09,.13,.07,.12,.05,.16,.08,.06,.14,.01,.09][i]
        total_liabilities=round(assets*leverage,2); market_cap=round(500+80*i,1)
        rows.append({"company_id":f"PUB-{group+1}{i+1:02d}","company_name":f"{['星河','远岑','松原','海岚','启明','北辰','嘉木','凌云','清源','卓越','云际','丰禾'][i]}{['数科','科技','系统','智联','信息','产业','新材','实业','科技','发展','集团','制造'][group] }（虚构）","industry":industry if close or i%3 else ("信息技术" if group==0 else "化工材料"),"company_description":variant[0] if close else f"虚构上市公司，主营{biz}相关业务。","main_business":variant[1] if close else f"主营{['IT咨询与系统集成','硬件设备销售','传统工业制造','综合商务服务'][i%4]}。","products":variant[2] if close else f"提供{['定制开发服务','终端设备、配件','通用工业材料','商业咨询服务'][i%4]}。","application_scenarios":variant[3] if close else "通用商业场景","customer_groups":variant[4] if close else f"{['大型政府机构','个人消费者','工业企业','多元化客户'][i%4]}","business_model":variant[5] if close else f"{['项目制服务','硬件一次性销售','直销制造','广告及平台服务'][i%4]}","revenue":round(investee_revenue:=investees[group]["revenue"]*([.45,.7,1.0,1.4,2.2,4.5,.2,1.8,.8,1.1,3.8,.55][i]),2),"ebitda":round(investee_revenue*ebitda_margin,2),"net_profit":round(investee_revenue*ebitda_margin*.68,2),"total_assets":assets,"total_liabilities":total_liabilities,"book_equity":round(assets-total_liabilities,2),"cash":round(assets*(.10+.01*(i%4)),2),"interest_bearing_debt":round(total_liabilities*.62,2),"minority_interest":round(assets*.01,2),"preferred_stock":0,"non_operating_investments":round(assets*.015,2),"debt_to_assets":leverage,"revenue_growth":[.22,.20,.25,.18,.27,.08,.31,.21,.16,.24,.05,.19][i],"gross_margin":[.66,.62,.7,.64,.58,.42,.75,.67,.55,.69,.3,.63][i] if group==0 else [.32,.29,.34,.27,.35,.23,.4,.31,.28,.33,.18,.3][i],"ebitda_margin":ebitda_margin,"stock_code":f"{600000+group*100+i}","market":"虚构主板","market_cap":market_cap,"valuation_multiple":[8,10,12,9,14,11,13,8.5,10.5,12.5,75,9.5][i],"valuation_multiple_type":"EV/EBITDA","reporting_period":"2025年度","valuation_date":"2025-12-31","currency":"CNY","amount_unit":"百万元","source_reference":"虚构演示数据，无外部来源"})
    clients=[]
    for group in range(2):
      for i in [0,4,5]: clients.append({"investee_company_name":investees[group]["company_name"],"comparable_company_name":rows[group*12+i]["company_name"]})
    return pd.DataFrame(investees),pd.DataFrame(rows),pd.DataFrame(clients)
if __name__=="__main__":
    ROOT.joinpath("sample_data").mkdir(exist_ok=True)
    a,b,c=build()
    with pd.ExcelWriter(ROOT/"sample_data"/"demo_input.xlsx",engine="openpyxl") as w:
      a.to_excel(w,sheet_name="investee",index=False);b.to_excel(w,sheet_name="candidates",index=False);c.to_excel(w,sheet_name="client_list",index=False)
    with pd.ExcelWriter(ROOT/"sample_data"/"demo_workpaper.xlsx",engine="openpyxl") as w:
      for company in a.company_name:
        pd.DataFrame({"候选公司":b.company_name,"综合得分": [None]*len(b),"AI建议": [None]*len(b),"合理性分析（回填目标）": [None]*len(b)}).to_excel(w,sheet_name=company[:31],index=False)
    print(f"created {ROOT/'sample_data'/'demo_input.xlsx'}")
