"""Fixed build/smoke entry points under Python -I -S; never process .pth files."""
import importlib.util,json,os,runpy,sys
from pathlib import Path

def main():
    if not sys.flags.isolated or not sys.flags.no_site:raise RuntimeError('Build bootstrap requires Python -I -S')
    mode,site,workspace,scripts=sys.argv[1:5]
    site=Path(site).resolve();workspace=Path(workspace).resolve();scripts=Path(scripts).resolve()
    if mode not in {'package','smoke','verify'} or not site.is_dir():raise RuntimeError('Unapproved build mode/input')
    sys.dont_write_bytecode=True;os.environ['PYTHONDONTWRITEBYTECODE']='1'
    # No site.addsitedir: executable .pth lines and user/global sites are absent.
    sys.path=[p for p in sys.path if p and 'site-packages' not in p]+[str(site)]
    if 'site' in sys.modules:raise RuntimeError('Site startup unexpectedly ran')
    os.chdir(workspace)
    if mode=='package':
        import PyInstaller.__main__
        PyInstaller.__main__.run(['--noconfirm','--distpath',str(workspace/'dist'),'--workpath',str(workspace/'build'),str(workspace/'Offline GPT.spec')])
    elif mode=='verify':
        spec=importlib.util.spec_from_file_location('accepted_inputs',scripts/'candidate_inputs.py')
        inputs=importlib.util.module_from_spec(spec);spec.loader.exec_module(inputs)
        repo,git,commit,package,manifest,manifest_sha,output=sys.argv[5:]
        result=inputs.associate(Path(repo),commit,Path(git),Path(package),Path(manifest),manifest_sha)
        with Path(output).open('x') as stream:json.dump(result,stream,indent=2)
    else:
        spec=importlib.util.spec_from_file_location('accepted_smoke',scripts/'candidate_smoke.py')
        smoke=importlib.util.module_from_spec(spec);spec.loader.exec_module(smoke)
        result=smoke.run(Path(sys.argv[5]),Path(sys.argv[6]),sys.argv[7])
        print(json.dumps(result));return 0 if result['status']=='PASSED' else 2
    return 0
if __name__=='__main__':raise SystemExit(main())
