"""Run readable-model fixtures with the compiled source as their reference."""
import json,sys,importlib,shutil,collections,time,os
from pathlib import Path
from native_ai import NativePort,START
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
WORK=ROOT/'work/native_model_checks'
class NativeReference(NativePort):
    def __init__(self,ram):
        self._as_counter=False
        self._visits_cache=None
        super().__init__(ram,coverage=True)
        self._as_counter=True
    @property
    def visits(self):
        if not self._as_counter:return self._coverage
        if self._visits_cache is None:
            self._visits_cache=collections.Counter({START+i*4:int(n) for i,n in enumerate(self._coverage) if n})
        return self._visits_cache
    @visits.setter
    def visits(self,value):self._coverage=value
    def call(self,*args,**kwargs):
        self._visits_cache=None
        return super().call(*args,**kwargs)

def link_file(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists():os.link(source,target)

def main():
    WORK.mkdir(exist_ok=True);(WORK/'reports').mkdir(exist_ok=True);(WORK/'work').mkdir(exist_ok=True)
    for p in (ROOT/'work').glob('*selector*.bin'):
        target=WORK/'work'/p.name
        link_file(p,target)
    link_file(ROOT/'work/ram_turn25.bin',WORK/'work/ram_turn25.bin')
    for folder in ['reports/card_database','work/decompiled/modduel_eng_relocated']:
        for source in (ROOT/folder).rglob('*'):
            if source.is_file():link_file(source,WORK/source.relative_to(ROOT))
    for name in ['module_analysis.json','modehsys_analysis.json']:
        link_file(ROOT/'reports'/name,WORK/'reports'/name)
    shutil.copy2(ROOT/'reports/trial4_selector_offline.json',WORK/'reports/trial4_selector_offline.json')
    modules=sys.argv[1:] or ['validate_ai','validate_targets','validate_phases','validate_battle','validate_summon_planning','validate_responses','validate_card_strategy']
    results=[]
    for name in modules:
        module=importlib.import_module(name);module.VM=NativeReference;module.ROOT=WORK
        if name=='validate_responses':importlib.import_module('response_model').ROOT=WORK
        begin=time.monotonic()
        # Model export helpers use the real DB path. Validation reports stay in
        # the isolated directory and never replace the original MIPS evidence.
        print('Native model check',name,flush=True)
        argv=sys.argv;sys.argv=[name]
        try:module.main()
        finally:sys.argv=argv
        reportfiles=sorted((WORK/'reports').glob('*validation.json'),key=lambda p:p.stat().st_mtime)
        latest=reportfiles[-1];report=json.loads(latest.read_text())
        target=ROOT/'reports'/('native_model_'+latest.name);target.write_text(json.dumps(report,indent=2))
        results.append(dict(suite=name,report=str(target),seconds=round(time.monotonic()-begin,2),mismatch_count=report.get('mismatch_count'),
            source='Compiled native C replaces the original-instruction VM for existing readable-model fixtures.'))
    (ROOT/'reports/native_model_validation.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
if __name__=='__main__':main()
