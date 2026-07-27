import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVASCRIPT = ROOT / "web" / "assets" / "dashboard.js"

PROBE = r'''
const fs = require("fs"), vm = require("vm");
const context = {window:{setTimeout,clearTimeout},document:{querySelector(){return null},addEventListener(){},removeEventListener(){},visibilityState:"visible"},Intl,Date,Object,Array,Math,Number,String,AbortController};
vm.createContext(context); vm.runInContext(fs.readFileSync("web/assets/dashboard.js","utf8"),context); const api=context.window.SolarDashboard;
function payload(){return {
 schema_version:"1.0",generated_at:"2026-07-27T17:04:10Z",data:{timestamp:"2026-07-27T17:04:02Z",latest_sample_at:"2026-07-27T17:04:02Z",age_seconds:7.6},
 status:{overall:"fresh",freshness:"fresh",affected_components:[],components:{solar_data:"fresh",weather:"fresh",battery:"available",heat:"available",chart:"available",insight:"available",historical_comparison:"available"}},
 header:{local_date:"2026-07-27",local_time:"2026-07-27T19:04:10+02:00",weather_condition:"sunny",sunshine_hours:8.2,sunrise:"05:58",sunset:"21:04"},
 live:{solar_power_kw:10.89,house_consumption_kw:3.2,heat_power_kw:1.1,battery_state_of_charge_percent:50,battery_flow:{power_kw:-1,magnitude_kw:1,direction:"discharging"},grid_flow:{power_kw:.5,magnitude_kw:.5,direction:"exporting"}},
 today:{yield_kwh:44.2,self_consumption_percent:67,co2_avoided_kg:5.4},
 chart:{interval_minutes:5,local_date:"2026-07-27",series:[{start_at:"2026-07-27T08:00:00Z",end_at:"2026-07-27T08:05:00Z",solar_power_kw:2,house_consumption_kw:1,battery_power_kw:-1,sample_count:4},{start_at:"2026-07-27T08:05:00Z",end_at:"2026-07-27T08:10:00Z",solar_power_kw:3,house_consumption_kw:null,battery_power_kw:.5,sample_count:4},{start_at:"2026-07-27T08:20:00Z",end_at:"2026-07-27T08:25:00Z",solar_power_kw:4,house_consumption_kw:2,battery_power_kw:null,sample_count:3}]},
 insight:{fact_id:"FACT",family:"solar",line_1:"Zeile eins",line_2:"Zeile zwei",selection_hour:"2026-07-27T19:00:00+02:00",persisted:true},
 historical_comparison:{type:"HIST",statement:"Höher als gestern.",direction:"higher",difference_percent:10,comparison_value_kwh:44,baseline_kwh:40,reference_period:"today",reference_day:"2026-07-26",comparison_day:null,baseline_days:null}
};}
function response(value){return {ok:true,json:async()=>value};} function root(){return {attrs:{"aria-busy":"true"},setAttribute(k,v){this.attrs[k]=v}};}
(async()=>{const good=payload(), missing=payload(); missing.header.sunrise=null; missing.header.sunset=null;
 const degraded=payload(); degraded.status.overall="degraded"; degraded.status.affected_components=["weather"]; degraded.status.components.weather="missing"; degraded.header.sunshine_hours=null; degraded.header.sunrise=null; degraded.header.sunset=null; const degradedView=api.buildViewModel(degraded);
 const normal=api.chartModel(good.chart.series), x=v=>v,y=v=>v;
 const invalid=[]; let p;
 p=payload();delete p.live.grid_flow;invalid.push(p);p=payload();p.data.age_seconds="8";invalid.push(p);p=payload();p.status.overall="online";invalid.push(p);p=payload();p.live.battery_flow.direction="full";invalid.push(p);p=payload();p.chart.series[0].start_at="bad";invalid.push(p);p=payload();p.insight.persisted="yes";invalid.push(p);
 let dom={}; const r=root(); let queue=[response(good),response(invalid[0]),new Error("network"),response(Object.assign(payload(),{data:{timestamp:"2026-07-27T17:04:22Z",latest_sample_at:"2026-07-27T17:04:22Z",age_seconds:2}}))];
 const controller=api.createController(r,{fetcher:async()=>{const next=queue.shift();if(next instanceof Error)throw next;return next},schedule:()=>0,cancel:()=>{},apply:(_root,view)=>{dom={fields:{...view.fields},segments:{...view.segments},chart:JSON.stringify(view.chart),status:view.statusLabel}},connection:(_root,state,label)=>{dom.connection={state,label}}});
 await controller.refresh();const afterGood=JSON.stringify(dom);await controller.refresh();const afterInvalid=JSON.stringify(dom);await controller.refresh();const afterNetwork=JSON.parse(JSON.stringify(dom));await controller.refresh();const afterRecovery=JSON.parse(JSON.stringify(dom));
 process.stdout.write(JSON.stringify({constants:[api.DATA_URL,api.SCHEMA_VERSION,api.REFRESH_MS,api.SOLAR_CAPACITY_KWP,api.TIME_ZONE],formats:[api.formatTime(null),api.formatDate(null),api.formatTime(undefined),api.formatDate(""),api.formatTime(false),api.formatDate("bad"),api.formatTime("06:02")],missingSun:api.buildViewModel(missing).fields,invalid:invalid.map(api.validPayload),degraded:{states:degradedView.states,fields:degradedView.fields,label:degradedView.statusLabel},normal:{bottom:normal.bottom,solar:api.pathSequences(normal,"solar",x,y).length,house:api.pathSequences(normal,"house",x,y).length,empty:api.chartModel([]).points.length},status:api.buildViewModel(good).statusLabel,afterGood,afterInvalid,afterNetwork,afterRecovery}));
})().catch(error=>{console.error(error);process.exit(1)});
'''

class WebDashboardJavaScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(["node", "-e", PROBE], cwd=ROOT, text=True, capture_output=True, check=True)
        cls.result = json.loads(result.stdout)

    def test_constants_and_null_safe_swiss_time_formatting(self):
        self.assertEqual(self.result["constants"], ["dashboard.json", "1.0", 15000, 21.78, "Europe/Zurich"])
        self.assertEqual(self.result["formats"], ["—", "—", "—", "—", "—", "—", "06:02"])
        self.assertEqual(self.result["missingSun"]["header.sunrise"], "—")
        self.assertEqual(self.result["missingSun"]["header.sunset"], "—")

    def test_incomplete_and_wrongly_typed_payloads_are_rejected(self):
        self.assertEqual(self.result["invalid"], [False] * 6)

    def test_valid_then_invalid_payload_is_fully_atomic(self):
        self.assertEqual(self.result["afterGood"], self.result["afterInvalid"])

    def test_network_failure_retains_values_and_uses_dashboard_timestamp(self):
        before = json.loads(self.result["afterGood"])
        offline = self.result["afterNetwork"]
        self.assertEqual(offline["fields"], before["fields"])
        self.assertEqual(offline["segments"], before["segments"])
        self.assertEqual(offline["chart"], before["chart"])
        self.assertEqual(offline["connection"], {"state": "offline", "label": "OFFLINE · STAND 19:04"})

    def test_successful_reconnection_and_fresh_age(self):
        self.assertEqual(self.result["status"], "LIVE · 8 s")
        self.assertEqual(self.result["afterRecovery"]["status"], "LIVE · 2 s")
        self.assertNotIn("connection", self.result["afterRecovery"])

    def test_degraded_marks_only_affected_weather_component(self):
        degraded = self.result["degraded"]
        self.assertEqual(degraded["states"]["weather"], "degraded")
        self.assertEqual(degraded["states"]["solar"], "fresh")
        self.assertNotIn("dashboard", degraded["states"])
        self.assertNotIn("system-status", degraded["states"])
        self.assertEqual(degraded["fields"]["header.sunrise"], "—")
        self.assertEqual(degraded["fields"]["header.sunset"], "—")
        self.assertEqual(degraded["label"], "EINGESCHRÄNKT · 8 s")

    def test_chart_gap_null_negative_and_empty_series(self):
        self.assertLess(self.result["normal"]["bottom"], 0)
        self.assertEqual(self.result["normal"]["solar"], 2)
        self.assertEqual(self.result["normal"]["house"], 2)
        self.assertEqual(self.result["normal"]["empty"], 0)

    def test_mobile_chart_geometry_does_not_distort_text(self):
        source = JAVASCRIPT.read_text(encoding="utf-8")
        self.assertIn('width = 360, height = 150', source)
        self.assertIn('preserveAspectRatio", "xMidYMid meet"', source)
        self.assertNotIn('preserveAspectRatio", "none"', source)

if __name__ == "__main__": unittest.main()
