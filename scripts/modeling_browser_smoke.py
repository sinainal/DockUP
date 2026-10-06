"""Chrome/CDP smoke test without adding a browser dependency to the app.

Run against a separate DockUP test server configured with an isolated modeling
data directory. Creates fresh PE/xTB jobs only, never modifies research reports.
"""
import argparse
import base64
import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import requests
from websockets.sync.client import connect


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--base-url",default="http://127.0.0.1:8127")
    parser.add_argument("--debug-port",type=int,default=9237)
    parser.add_argument("--output",default="output/modeling_browser_20261006")
    parser.add_argument("--template",required=True)
    args=parser.parse_args()
    output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dockup-modeling-chrome-",ignore_cleanup_errors=True) as profile:
        chrome=subprocess.Popen(["/usr/bin/google-chrome","--headless=new","--no-sandbox","--disable-dev-shm-usage","--use-angle=swiftshader","--enable-unsafe-swiftshader","--remote-allow-origins=*",f"--remote-debugging-port={args.debug_port}",f"--user-data-dir={profile}","about:blank"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        try:
            endpoint=f"http://127.0.0.1:{args.debug_port}"
            deadline=time.monotonic()+15
            while True:
                try:target=requests.put(endpoint+"/json/new?about:blank",timeout=2).json();break
                except requests.RequestException:
                    if time.monotonic()>deadline:raise
                    time.sleep(.1)
            errors=[]
            with connect(target["webSocketDebuggerUrl"],max_size=20*1024*1024) as socket:
                sequence=0
                def call(method,params=None):
                    nonlocal sequence
                    sequence+=1;key=sequence;socket.send(json.dumps({"id":key,"method":method,"params":params or {}}))
                    while True:
                        data=json.loads(socket.recv(timeout=45))
                        if data.get("method")=="Runtime.exceptionThrown":errors.append(data["params"])
                        if data.get("id")==key:
                            if data.get("error"):raise RuntimeError(data["error"])
                            return data.get("result",{})
                def evaluate(expression):
                    result=call("Runtime.evaluate",{"expression":expression,"returnByValue":True,"awaitPromise":True})
                    if result.get("exceptionDetails"):raise RuntimeError(result["exceptionDetails"])
                    return result.get("result",{}).get("value")
                def until(expression,seconds=45):
                    end=time.monotonic()+seconds
                    while time.monotonic()<end:
                        value=evaluate(expression)
                        if value:return value
                        time.sleep(.2)
                    diagnostic=evaluate("({notice:document.getElementById('qmNotice')?.textContent,meta:document.getElementById('qmViewerMeta')?.textContent,outputs:document.getElementById('qmOutputStrip')?.textContent,title:document.getElementById('qmViewerTitle')?.textContent})")
                    (output/"browser_failure.json").write_text(json.dumps({"condition":expression,"diagnostic":diagnostic,"errors":errors},indent=2))
                    screenshot("browser_failure.png")
                    raise AssertionError("Browser condition expired: "+expression)
                def screenshot(name):
                    result=call("Page.captureScreenshot",{"format":"png","captureBeyondViewport":False})
                    (output/name).write_bytes(base64.b64decode(result["data"]))
                call("Runtime.enable");call("Page.enable")
                call("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1080,"deviceScaleFactor":1,"mobile":False})
                call("Page.navigate",{"url":args.base_url+"/quantum"})
                until("document.getElementById('qmCapabilities')?.textContent.includes('ready')")
                until("!!document.querySelector('#qmViewport canvas')")
                created=time.time()
                evaluate("document.querySelector('#qmBuild [name=repeats]').value='2';document.querySelector('#qmBuild [name=conformers]').value='2';document.getElementById('qmBuild').requestSubmit();true")
                fresh=until(f"fetch('/api/modeling/jobs').then(r=>r.json()).then(rows=>rows.find(j=>j.kind==='build' && j.created>{created} && j.state==='succeeded'))")
                model_id=fresh["result"]["records"][0]["model"]["id"]
                selector=f'#qmModels [data-select="{model_id}"]'
                until("!!document.querySelector("+json.dumps(selector)+")")
                evaluate("document.querySelector("+json.dumps(selector)+").click();true")
                until("document.getElementById('qmViewerMeta').textContent.includes('original geometry')")
                evaluate("document.querySelector('[data-tab=calculate]').click();document.getElementById('qmCalculate').requestSubmit();true")
                calculation=until("fetch('/api/modeling/jobs').then(r=>r.json()).then(rows=>rows.find(j=>j.kind==='quantum' && j.payload.model_ids?.includes("+json.dumps(model_id)+") && j.state==='succeeded'))")
                selector=f'#qmResults [data-result-job="{calculation["id"]}"]'
                until("!!document.querySelector("+json.dumps(selector)+")")
                evaluate("document.querySelector("+json.dumps(selector)+").click();true")
                until("document.querySelectorAll('#qmOutputStrip [data-artifact]').length>0")
                until("[...document.querySelectorAll('#qmOutputStrip [data-artifact]')].some(e=>e.textContent==='xtb_optimized')")
                evaluate("[...document.querySelectorAll('#qmOutputStrip [data-artifact]')].find(e=>e.textContent==='xtb_optimized').click();true")
                until("document.getElementById('qmViewerMeta').textContent.startsWith('xtb_optimized')")
                evaluate("[...document.querySelectorAll('#qmDetails button')].find(e=>e.textContent==='Use for next calculation').click();true")
                until("document.getElementById('qmViewerTitle').textContent.includes('xtb_optimized') && document.getElementById('qmNotice').textContent.includes('new model')")
                assert evaluate("document.documentElement.scrollWidth<=innerWidth+2"),"Desktop horizontal overflow"
                screenshot("quantum_desktop.png")
                call("Emulation.setDeviceMetricsOverride",{"width":390,"height":844,"deviceScaleFactor":1,"mobile":True})
                evaluate("window.dispatchEvent(new Event('resize'));true")
                time.sleep(.5)
                assert evaluate("document.documentElement.scrollWidth<=innerWidth+2"),"Mobile horizontal overflow"
                screenshot("quantum_mobile.png")
                call("Emulation.setDeviceMetricsOverride",{"width":1440,"height":1080,"deviceScaleFactor":1,"mobile":False})
                call("Page.navigate",{"url":args.base_url+"/"})
                until("!!document.getElementById('openHomologyPopup')")
                evaluate("document.getElementById('openHomologyPopup').click();true")
                until("document.getElementById('hmDialog')?.open")
                until("!!document.querySelector('#hmViewport canvas')")
                node=call("DOM.getDocument",{})["root"]["nodeId"]
                input_node=call("DOM.querySelector",{"nodeId":node,"selector":"#hmTemplate"})["nodeId"]
                call("DOM.setFileInputFiles",{"nodeId":input_node,"files":[str(Path(args.template).resolve())]})
                until("document.querySelectorAll('#hmTargets .hm-target-row').length===1")
                evaluate("document.getElementById('hmPreview').click();true")
                until("document.getElementById('hmAlignment').textContent.includes('Identity')")
                screenshot("homology_popup.png")
                assert evaluate("document.getElementById('hmDialog').getBoundingClientRect().right<=innerWidth"),"Dialog outside viewport"
                report={"base_url":args.base_url,"desktop_and_mobile_no_horizontal_overflow":True,"ngl_canvas_loaded":True,"fresh_PE_model_id":model_id,"real_PE_build_and_xtb_ui_flow":True,"optimized_geometry_derived_for_next_calculation":True,"homology_template_upload_alignment_preview":True,"uncaught_runtime_errors":errors,"screenshots":["quantum_desktop.png","quantum_mobile.png","homology_popup.png"]}
                (output/"browser_audit.json").write_text(json.dumps(report,indent=2))
                if errors:raise AssertionError("Uncaught browser errors: "+json.dumps(errors)[:1200])
                print(json.dumps(report,indent=2))
        finally:
            try:os.killpg(chrome.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:chrome.wait(timeout=5)
            except subprocess.TimeoutExpired:chrome.kill();chrome.wait()
            try:os.killpg(chrome.pid,signal.SIGKILL)
            except ProcessLookupError:pass


if __name__=="__main__":main()
