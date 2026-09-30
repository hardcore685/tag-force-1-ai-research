"""Build the generated C into a native library using the private compiler."""
import json,subprocess,concurrent.futures,hashlib,time,argparse,shutil
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--jobs',type=int,default=3)
    p.add_argument('--opt',choices=['0','1','2'],default='1')
    p.add_argument('--zig',type=Path,help='Path to a Zig executable; the tested compiler is Zig 0.16.0.')
    args=p.parse_args()
    config=ROOT/'work/toolchain/paths.json'
    paths=json.loads(config.read_text()) if config.exists() else {}
    zig=str(args.zig) if args.zig else paths.get('zig_executable') or shutil.which('zig')
    if not zig:raise SystemExit('Specify the compiler with --zig C:\\path\\to\\zig.exe')
    generated=HERE/'generated';out=HERE/'build';out.mkdir(exist_ok=True)
    manifest=json.loads((generated/'generation_manifest.json').read_text())
    begin=time.monotonic()
    def compile(name):
        source=generated/name;obj=out/(source.stem+'.obj');log=out/(source.stem+'.log')
        command=[zig,'cc','-std=c11','-O'+args.opt,'-fno-strict-aliasing','-ffp-contract=off','-c',str(source),'-o',str(obj)]
        result=subprocess.run(command,capture_output=True,text=True)
        log.write_text(result.stdout+result.stderr,encoding='utf-8')
        if result.returncode:raise RuntimeError(f'Compile failed for {name}: {log.read_text()[:1500]}')
        print('Compiled',name,flush=True);return obj
    # Initialize Zig's common compiler cache once before concurrent invocations.
    first=compile(manifest['source_files'][0])
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        objects=[first,*pool.map(compile,manifest['source_files'][1:])]
    library=out/'tagforce_ai.dll'
    result=subprocess.run([zig,'cc','-shared',*[str(o) for o in objects],'-o',str(library)],capture_output=True,text=True)
    (out/'link.log').write_text(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError('Link failed: '+result.stderr[:2000])
    report=dict(library=str(library),size=library.stat().st_size,sha256=hashlib.sha256(library.read_bytes()).hexdigest(),
        source_count=len(objects),seconds=round(time.monotonic()-begin,2),optimization=args.opt,
        compiler=json.loads((ROOT/'work/toolchain/zig_provenance.json').read_text()) if (ROOT/'work/toolchain/zig_provenance.json').exists() else dict(executable=zig),
        warning='Compilation confirms valid native source, not behavioral equivalence.')
    (ROOT/'reports/native_build.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2),flush=True)
if __name__=='__main__':main()
