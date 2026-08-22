import json
import subprocess
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVASCRIPT = ROOT / "web" / "assets" / "dashboard.js"

PAYLOAD = {
    "schema_version": "1.0", "generated_at": "2026-07-27T17:04:10Z",
    "data": {"timestamp": "2026-07-27T17:04:02Z", "latest_sample_at": "2026-07-27T17:04:02Z", "age_seconds": 7.6},
    "status": {"overall": "fresh", "freshness": "fresh", "affected_components": [], "components": {
        "solar_data": "fresh", "weather": "fresh", "battery": "available", "heat": "available",
        "chart": "available", "insight": "available", "historical_comparison": "available"}},
    "header": {"local_date": "2026-07-27", "local_time": "2026-07-27T19:04:10+02:00", "weather_condition": "sunny", "sunshine_hours": 8.2, "sunrise": "05:58", "sunset": "21:04"},
    "live": {"solar_power_kw": 10.89, "house_consumption_kw": 3.2, "heat_power_kw": 1.1, "battery_state_of_charge_percent": 50,
             "battery_flow": {"power_kw": -1, "magnitude_kw": 1, "direction": "discharging"},
             "grid_flow": {"power_kw": .5, "magnitude_kw": .5, "direction": "exporting"}},
    "today": {"yield_kwh": 44.2, "self_consumption_percent": 67, "co2_avoided_kg": 5.4},
    "chart": {"interval_minutes": 5, "local_date": "2026-07-27", "series": [
        {"start_at": "2026-07-27T08:00:00Z", "end_at": "2026-07-27T08:05:00Z", "solar_power_kw": 2, "house_consumption_kw": 1, "grid_import_kw": 0, "battery_state_of_charge_percent": 55, "battery_power_kw": -1, "sample_count": 4},
        {"start_at": "2026-07-27T08:05:00Z", "end_at": "2026-07-27T08:10:00Z", "solar_power_kw": 3, "house_consumption_kw": None, "grid_import_kw": 1.5, "battery_state_of_charge_percent": 56, "battery_power_kw": .5, "sample_count": 4},
        {"start_at": "2026-07-27T08:20:00Z", "end_at": "2026-07-27T08:25:00Z", "solar_power_kw": 4, "house_consumption_kw": 2, "grid_import_kw": 0, "battery_state_of_charge_percent": None, "battery_power_kw": None, "sample_count": 3}]},
    "insight": {"fact_id": "FACT", "family": "solar", "line_1": "Zeile eins", "line_2": "Zeile zwei", "selection_hour": "2026-07-27T19:00:00+02:00", "persisted": True},
    "historical_comparison": {"type": "HIST", "statement": "Höher als gestern.", "direction": "higher", "difference_percent": 10, "comparison_value_kwh": 44, "baseline_kwh": 40, "reference_period": "today", "reference_day": "2026-07-26", "comparison_day": None, "baseline_days": None},
}

JS_BOOT = r'''
const fs=require("fs"),vm=require("vm");
class Node {constructor(tag="div"){this.tag=tag;this.attrs={};this.children=[];this.textContent="";this.clientWidth=280;this.clientHeight=125}setAttribute(k,v){this.attrs[k]=String(v)}removeAttribute(k){delete this.attrs[k]}appendChild(n){this.children.push(n);return n}replaceChildren(...nodes){this.children=[...nodes]}get lastChild(){return this.children[this.children.length-1]}}
const document={querySelector(){return null},createElement:t=>new Node(t),createElementNS:(_n,t)=>new Node(t),addEventListener(){},removeEventListener(){},visibilityState:"visible"};
const context={window:{setTimeout,clearTimeout},document,Intl,Date,Object,Array,Math,Number,String,AbortController};vm.createContext(context);vm.runInContext(fs.readFileSync("web/assets/dashboard.js","utf8"),context);const api=context.window.SolarDashboard;
function clone(v){return JSON.parse(JSON.stringify(v))} function response(v){return {ok:true,json:async()=>v}}
'''

DOM_HELPER = r'''
function dashboard(){
 const fields=["header.local_date","header.local_time","header.sunshine_hours","header.sunrise","header.sunset","live.solar_power_kw","live.house_consumption_kw","live.heat_power_kw","live.battery_state_of_charge_percent","live.battery_flow.direction","live.battery_flow.magnitude_kw","live.grid_flow.direction","live.grid_flow.magnitude_kw","today.yield_kwh","today.self_consumption_percent","today.co2_avoided_kg","insight.line_1","insight.line_2","historical_comparison.statement","chart.series","data.timestamp"];
 const components=["solar","house-consumption","grid-flow","today","battery","battery-flow","heat","weather","chart","insight","historical-comparison","data-status","system-status"];
 const root=new Node("main"),map={};fields.forEach(k=>map[`[data-field="${k}"]`]=new Node());components.forEach(k=>map[`[data-component="${k}"]`]=new Node());
 map['[data-field="chart.series"]'].clientWidth=280;map['[data-field="chart.series"]'].clientHeight=125;
 map['[data-component="data-status"]'].appendChild(new Node("dot"));map['[data-component="data-status"]'].appendChild({textContent:"Daten werden geladen"});map['[data-component="system-status"]'].querySelector=s=>map[s];
 const solar=Array.from({length:20},()=>new Node("span")),battery=Array.from({length:20},()=>new Node("span"));
 root.querySelector=s=>map[s];root.querySelectorAll=s=>s==='[data-segment]'?[...solar,...battery]:s.includes('solar_power_kw')?solar:s.includes('battery_state')?battery:[];root.setAttribute("data-state","loading");root.setAttribute("aria-busy","true");
 return {root,map,solar,battery};
}
function snap(dom){const m=dom.map,components={};for(const [key,node] of Object.entries(m))if(key.includes('data-component'))components[key]=node.attrs["data-state"]||null;return {root:{...dom.root.attrs},components,solar:dom.solar.filter(n=>n.attrs["data-active"]).length,battery:dom.battery.filter(n=>n.attrs["data-active"]).length,solarValue:m['[data-field="live.solar_power_kw"]'].textContent,chartLabel:m['[data-field="chart.series"]'].attrs["aria-label"],chartChildren:m['[data-field="chart.series"]'].children.length,status:m['[data-component="data-status"]'].lastChild.textContent,footer:m['[data-field="data.timestamp"]'].textContent}}
'''


def run_js(source):
    result = subprocess.run(["node", "-e", source], cwd=ROOT, text=True, capture_output=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class WebDashboardJavaScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        payload = json.dumps(PAYLOAD, ensure_ascii=False)
        pure = JS_BOOT + f"const good={payload};" + r'''
const weather=clone(good);weather.status.overall="degraded";weather.status.affected_components=["weather"];weather.status.components.weather="invalid";weather.header.weather_condition=null;weather.header.sunshine_hours=null;weather.header.sunrise=null;weather.header.sunset=null;
const invalid=[];let p;p=clone(good);delete p.live.grid_flow;invalid.push(p);p=clone(good);p.data.age_seconds="8";invalid.push(p);p=clone(good);p.schema_version="2.0";invalid.push(p);p=clone(good);p.live.battery_flow.direction="full";invalid.push(p);p=clone(good);p.chart.series[0].start_at="bad";invalid.push(p);p=clone(good);p.chart.series[0].battery_state_of_charge_percent=101;invalid.push(p);
const model=api.chartModel(good.chart.series),x=v=>v,y=v=>v,weatherView=api.buildViewModel(weather),withoutComparison=clone(good);withoutComparison.historical_comparison=null;withoutComparison.status.components.historical_comparison="missing";const emptyComparisonView=api.buildViewModel(withoutComparison);
const thresholdRows=[.4,.49,.5,1.2,0].map((value,index)=>({...good.chart.series[0],start_at:`2026-07-27T08:${String(index*5).padStart(2,"0")}:00Z`,grid_import_kw:value}));
const thresholdModel=api.chartModel(thresholdRows);
const clippingModel={top:20,bottom:-5},axisLayouts=[280,350,780,1200].map(width=>api.chartLayout({clientWidth:width,clientHeight:125},clippingModel));
process.stdout.write(JSON.stringify({emptyComparisonView,axisLayouts,formats:[api.formatTime(null),api.formatDate(null),api.formatTime(undefined),api.formatDate(""),api.formatTime(false),api.formatDate("bad")],invalid:invalid.map(api.validPayload),weatherValid:api.validPayload(weather),weather:weatherView,chart:{bottom:model.bottom,solar:api.pathSequences(model,"solar",x,y).length,house:api.pathSequences(model,"house",x,y).length,gridImport:api.pathSequences(model,"gridImport",x,y).length,gridValues:model.points.map(point=>point.gridImport),gridThresholdValues:thresholdModel.points.map(point=>point.gridImport),battery:api.pathSequences(model,"battery",x,y).length,batteryValues:model.points.map(point=>point.battery)},geometry320:api.chartGeometry({clientWidth:280,clientHeight:125}),geometry390:api.chartGeometry({clientWidth:350,clientHeight:125}),timeTicksMobile:api.chartTimeTicks(256),timeTicksWide:api.chartTimeTicks(326)}));'''
        cls.result = run_js(pure)

        controller = JS_BOOT + DOM_HELPER + f"const good={payload};" + r'''
(async()=>{
 const initial=dashboard(),badSchema=clone(good);badSchema.schema_version="2.0";const initialController=api.createController(initial.root,{fetcher:async()=>response(badSchema),schedule:()=>0,cancel:()=>{}});await initialController.refresh();
 const dom=dashboard(),wrongSchema=clone(good);wrongSchema.schema_version="2.0";const wrongType=clone(good);wrongType.live.solar_power_kw="ten";const structural=clone(good);delete structural.today;
 const recovered=clone(good);recovered.data.age_seconds=2;recovered.live.solar_power_kw=12;
 const queue=[response(good),response(wrongSchema),response(structural),response(wrongType),{ok:false,status:503,json:async()=>good},{ok:true,json:async()=>{throw new SyntaxError("JSON")}},new Error("network"),response(recovered)];
 const controller=api.createController(dom.root,{fetcher:async()=>{const item=queue.shift();if(item instanceof Error)throw item;return item},schedule:()=>0,cancel:()=>{}});const states=[];for(let i=0;i<8;i++){await controller.refresh();states.push(snap(dom))}
 const degraded=dashboard(),weather=clone(good);weather.status.overall="degraded";weather.status.affected_components=["weather"];weather.status.components.weather="invalid";weather.header.weather_condition=null;weather.header.sunshine_hours=null;weather.header.sunrise=null;weather.header.sunset=null;const degradedController=api.createController(degraded.root,{fetcher:async()=>response(weather),schedule:()=>0,cancel:()=>{}});await degradedController.refresh();
 process.stdout.write(JSON.stringify({initial:snap(initial),states,degraded:snap(degraded)}));
})().catch(e=>{console.error(e);process.exit(1)});'''
        cls.dom = run_js(controller)

    def test_null_safe_time_formatting_and_real_weather_invalid_payload(self):
        self.assertEqual(self.result["formats"], ["—"] * 6)
        self.assertTrue(self.result["weatherValid"])
        weather = self.result["weather"]
        self.assertEqual(weather["states"]["weather"], "degraded")
        for component in ("solar", "house-consumption", "grid-flow", "today", "battery", "battery-flow", "heat", "chart", "insight", "historical-comparison"):
            self.assertNotEqual(weather["states"][component], "degraded")
        self.assertEqual(weather["fields"]["header.sunrise"], "—")

    def test_missing_historical_comparison_renders_explanatory_empty_state(self):
        view = self.result["emptyComparisonView"]
        self.assertEqual(view["fields"]["historical_comparison.statement"],
                         "Noch keine Vergleichsdaten verfügbar")
        self.assertEqual(view["states"]["historical-comparison"], "missing")
        self.assertEqual(view["directions"]["comparison"], "unavailable")

    def test_incomplete_wrong_schema_and_wrong_types_are_rejected(self):
        self.assertEqual(self.result["invalid"], [False] * 6)

    def test_invalid_initial_payload_ends_loading_as_offline(self):
        initial = self.dom["initial"]
        self.assertEqual(initial["root"]["data-state"], "offline")
        self.assertEqual(initial["root"]["aria-busy"], "false")
        self.assertEqual(initial["solar"], 0)
        self.assertEqual(initial["battery"], 0)
        self.assertEqual(initial["chartLabel"], "Tagesverlauf nicht verfügbar")
        self.assertEqual(initial["status"], "OFFLINE")

    def test_all_post_success_failures_retain_complete_dom_and_go_offline(self):
        good, *failures, recovered = self.dom["states"]
        for failed in failures:
            self.assertEqual(failed["solarValue"], good["solarValue"])
            self.assertEqual(failed["solar"], good["solar"])
            self.assertEqual(failed["battery"], good["battery"])
            self.assertEqual(failed["chartChildren"], good["chartChildren"])
            self.assertEqual(failed["status"], "OFFLINE · STAND 19:04")
            self.assertEqual(failed["root"]["aria-busy"], "false")
        self.assertEqual(recovered["solarValue"], "12.0")
        self.assertEqual(recovered["status"], "LIVE · 2 s")
        self.assertEqual(recovered["root"]["data-state"], "fresh")

    def test_wrong_schema_after_success_uses_offline_path(self):
        self.assertEqual(self.dom["states"][1]["status"], "OFFLINE · STAND 19:04")

    def test_structural_and_type_errors_after_success_use_offline_path(self):
        self.assertEqual(self.dom["states"][2]["root"]["data-state"], "offline")
        self.assertEqual(self.dom["states"][3]["root"]["data-state"], "offline")

    def test_http_json_and_network_errors_share_offline_path(self):
        for index in (4, 5, 6):
            self.assertEqual(self.dom["states"][index]["status"], "OFFLINE · STAND 19:04")

    def test_valid_response_recovers_after_all_error_classes(self):
        recovered = self.dom["states"][7]
        self.assertEqual(recovered["root"]["data-state"], "fresh")
        self.assertEqual(recovered["status"], "LIVE · 2 s")

    def test_degraded_is_component_scoped_in_the_dom(self):
        degraded = self.dom["degraded"]
        self.assertEqual(degraded["root"]["data-state"], "fresh")
        self.assertEqual(degraded["components"]['[data-component="system-status"]'], "fresh")
        self.assertEqual(degraded["components"]['[data-component="data-status"]'], "degraded")
        self.assertEqual(degraded["components"]['[data-component="weather"]'], "degraded")
        self.assertEqual(degraded["components"]['[data-component="solar"]'], "fresh")
        self.assertEqual(degraded["status"], "EINGESCHRÄNKT · 8 s")

    def test_y_axis_labels_stay_inside_viewbox_at_all_target_widths(self):
        for layout in self.result["axisLayouts"]:
            self.assertGreaterEqual(layout["labelStart"], 0)
            self.assertGreater(layout["left"], layout["labelWidth"])
            self.assertLess(layout["left"], layout["width"] - layout["right"])
            self.assertLessEqual(layout["batteryLabelEnd"], layout["width"])
            self.assertEqual(layout["axisLabels"],
                             ["20.0 kW", "13.8 kW", "7.5 kW", "1.3 kW", "-5.0 kW"])
            self.assertEqual(layout["batteryAxisLabels"],
                             ["100 %", "75 %", "50 %", "25 %", "0 %"])

    def test_chart_gap_battery_percent_and_mobile_font_geometry(self):
        self.assertEqual(self.result["chart"]["bottom"], 0)
        self.assertEqual(self.result["chart"]["solar"], 2)
        self.assertEqual(self.result["chart"]["house"], 2)
        self.assertEqual(self.result["chart"]["gridImport"], 1)
        self.assertEqual(self.result["chart"]["gridValues"], [None, 1.5, None])
        self.assertEqual(self.result["chart"]["gridThresholdValues"],
                         [None, None, 0.5, 1.2, None])
        self.assertEqual(self.result["chart"]["battery"], 1)
        self.assertEqual(self.result["chart"]["batteryValues"], [55, 56, None])
        self.assertEqual(self.result["timeTicksMobile"], [0, 480, 960, 1440])
        self.assertEqual(self.result["timeTicksWide"], [0, 360, 720, 1080, 1440])
        for geometry in (self.result["geometry320"], self.result["geometry390"]):
            self.assertGreaterEqual(geometry["fontSize"], 10)
            self.assertGreaterEqual(geometry["width"], 240)
        source = JAVASCRIPT.read_text(encoding="utf-8")
        self.assertIn('preserveAspectRatio", "xMidYMid meet"', source)
        self.assertNotIn('preserveAspectRatio", "none"', source)


if __name__ == "__main__":
    unittest.main()

class NewDashboardViewModelTest(unittest.TestCase):
    def test_progress_constant_and_heat_threshold_are_typed(self):
        source = (Path(__file__).parents[1] / "web/assets/dashboard.js").read_text()
        self.assertIn("SOLAR_PROGRESS_MAX_KW = 18.0", source)
        self.assertIn("payload.live.heat_power_kw >= 0.05", source)
        self.assertNotIn("SOLAR_CAPACITY_KWP", source)

    def test_heat_arrow_threshold_uses_raw_numeric_value_and_availability(self):
        payload = json.dumps(PAYLOAD, ensure_ascii=False)
        result = run_js(JS_BOOT + f"const good={payload};" + r'''
const cases=[null,0,0.049,0.05,2.6].map(value=>{const p=clone(good);p.live.heat_power_kw=value;return api.buildViewModel(p).directions.heat});
const missing=clone(good);missing.status.components.heat="missing";missing.live.heat_power_kw=2.6;
process.stdout.write(JSON.stringify({cases,missing:api.buildViewModel(missing).directions.heat}));''')
        self.assertEqual(result["cases"], ["idle", "idle", "idle", "active", "active"])
        self.assertEqual(result["missing"], "idle")

    def test_progress_segments_and_uncapped_display_value(self):
        payload = json.dumps(PAYLOAD, ensure_ascii=False)
        result = run_js(JS_BOOT + f"const good={payload};" + r'''
const values=[0,9,18,22].map(value=>{const p=clone(good);p.live.solar_power_kw=value;const view=api.buildViewModel(p);return [view.segments.solar,view.fields["live.solar_power_kw"]]});
process.stdout.write(JSON.stringify(values));''')
        self.assertEqual(result, [[0, "0.0"], [10, "9.0"], [20, "18.0"], [20, "22.0"]])
