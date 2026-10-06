"""Live modeling CLI: same API and validation as the native DockUP UI."""
import argparse
import json
import sys
import time
from pathlib import Path

import requests


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dockup modeling", description="xTB/DFT workbench and template-based homology; live DockUP required")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status","models","pose-sets","jobs"):
        sub.add_parser(name)
    for name in ("build","poses","quantum","experiment"):
        child=sub.add_parser(name)
        child.add_argument("--payload", help="JSON object")
        child.add_argument("--file", help="JSON file")
        child.add_argument("--wait", action="store_true")
        child.add_argument("--wait-seconds", type=int, default=900)
    for name in ("show","cancel","retry"):
        child=sub.add_parser(name); child.add_argument("job_id")
    export=sub.add_parser("export"); export.add_argument("--output", required=True)
    for name in ("publish","derive"):
        child=sub.add_parser(name); child.add_argument("job_id"); child.add_argument("--artifact",type=int,required=True)
    archive=sub.add_parser("archive");archive.add_argument("--source",choices=["standalone","private"],default="standalone")
    archive.add_argument("--import-model",nargs="+",help="Explicit IDs to import; omitted lists the archive")
    xtb=sub.add_parser("xtb"); xtb.add_argument("--model-id",nargs="+",required=True);xtb.add_argument("--type",choices=["optimization","singlepoint","interaction"],default="optimization");xtb.add_argument("--solvent",choices=["water","none"],default="water");xtb.add_argument("--charge",type=int);xtb.add_argument("--uhf",type=int,default=0);xtb.add_argument("--threads",type=int,default=2);xtb.add_argument("--seconds",type=int,default=300);xtb.add_argument("--surface-fixed",action="store_true")
    dft=sub.add_parser("dft");dft.add_argument("--model-id",nargs="+",required=True);dft.add_argument("--engine",choices=["orca","pyscf"],default="orca");dft.add_argument("--type",choices=["singlepoint","optimization","frequency","interaction"],default="singlepoint");dft.add_argument("--solvent",choices=["water","none"],default="water");dft.add_argument("--charge",type=int);dft.add_argument("--multiplicity",type=int,default=1);dft.add_argument("--threads",type=int,default=2);dft.add_argument("--memory-mb",type=int,default=2048);dft.add_argument("--seconds",type=int,default=600)
    hm=sub.add_parser("homology");hsub=hm.add_subparsers(dest="homology_command",required=True)
    upload=hsub.add_parser("template");upload.add_argument("path")
    for name in ("alignment","build"):
        child=hsub.add_parser(name);child.add_argument("--payload");child.add_argument("--file")
    args=parser.parse_args(argv)
    base=args.base_url.rstrip("/")+"/api/modeling/"
    def call(path,body=None,files=None):
        response=requests.get(base+path,timeout=35) if body is None and files is None else requests.post(base+path,json=body,files=files,timeout=35)
        if not response.ok: raise RuntimeError(response.text)
        return response.json()
    def payload():
        if bool(args.payload)==bool(args.file): raise ValueError("Provide exactly one of --payload or --file")
        data=json.loads(Path(args.file).read_text() if args.file else args.payload)
        if not isinstance(data,dict):raise ValueError("Payload must be a JSON object")
        return data
    try:
        cmd=args.command
        if cmd in {"status","models","pose-sets","jobs"}: result=call("capabilities" if cmd=="status" else cmd)
        elif cmd in {"build","poses","quantum","experiment"}:
            result=call({"build":"models","poses":"pose-sets","quantum":"quantum/jobs","experiment":"experiments"}[cmd],payload())
            if args.wait:
                deadline=time.monotonic()+args.wait_seconds
                while result["state"] in {"queued","running"}:
                    if time.monotonic()>deadline: raise RuntimeError("CLI wait expired; server job remains tracked. Use show/cancel.")
                    time.sleep(1);result=call("jobs/"+result["id"])
        elif cmd in {"xtb","dft"}:
            data={"model_ids":args.model_id,"engine":"xtb" if cmd=="xtb" else args.engine,"calculation":args.type,"solvent":args.solvent,"charge":args.charge,"threads":args.threads,"timeout_seconds":args.seconds}
            if cmd=="xtb":data.update(uhf=args.uhf,optimization_mode="surface_fixed" if args.surface_fixed else "full")
            else:data.update(multiplicity=args.multiplicity,memory_mb=args.memory_mb)
            result=call("quantum/jobs",data)
        elif cmd in {"show","cancel","retry"}:result=call("jobs/"+args.job_id+({"show":"","cancel":"/cancel","retry":"/retry"}[cmd]),None if cmd=="show" else {})
        elif cmd in {"publish","derive"}:result=call("jobs/"+args.job_id+"/"+cmd,{"artifact_index":args.artifact})
        elif cmd=="archive":result=call("archive/import",{"source":args.source,"model_ids":args.import_model}) if args.import_model else call("archive?source="+args.source)
        elif cmd=="export":
            response=requests.get(base+"results.csv",timeout=35);response.raise_for_status();Path(args.output).write_text(response.text);result={"output":args.output}
        elif cmd=="homology":
            if args.homology_command=="template":
                with Path(args.path).open("rb") as stream:result=call("homology/templates",files={"file":(Path(args.path).name,stream)})
            else:result=call("homology/"+("alignment" if args.homology_command=="alignment" else "jobs"),payload())
        print(json.dumps(result,indent=2,ensure_ascii=False))
        return 1 if isinstance(result,dict) and result.get("state") in {"failed","timed_out","cancelled","interrupted","partially_succeeded"} else 0
    except (requests.RequestException,ValueError,OSError,RuntimeError) as exc:
        print(json.dumps({"error":str(exc)},ensure_ascii=False),file=sys.stderr);return 2


if __name__=="__main__":raise SystemExit(main())
