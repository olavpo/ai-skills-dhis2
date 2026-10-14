"""One command for every step of a review:  python3 run.py [--config pireview.json] <step> [args...]

The config's "type" (default program-indicators) picks the type adapter in scripts/types/<type>/. Core steps run the
scripts outside types/; type steps run the type's own script. Remaining args are passed through unchanged; --config
(anywhere on the line) is also exported as $PIREVIEW_CONFIG. A step the type doesn't support exits with status 2.
`python3 run.py steps` prints the table below with the script each step runs for the configured type.

  phase     step              runs                                   what
  validate  lint              types/<type>/lint.py                   static checks -> findings.json
            syntax            types/<type>/syntax.py                 server check of every text -> syntax.json
            gen               types/<type>/gen.py                    synthetic data with witnesses -> truth.json ...
            import-data       types/<type>/import_data.py            test org units, data import, analytics
            query             types/<type>/query.py                  analytics per item -> analytics.json
            compare           types/<type>/compare.py                engine check -> compare.json
            assemble          validate/assemble.py                   status per item, findings, write-up skeleton
            probe             types/<type>/probe.py                  verify an engine behaviour with probe objects
  review    topics            review/build_review.py                 meeting topics -> review.json, <p>-review.html
            dictionary        types/<type>/build_dictionary.py,      plain-language dictionary -> <p>-dictionary.html
                              then types/<type>/assemble_dict.py     (--descriptions DIR goes to assemble_dict.py)
            decisions         review/write_decisions.py              meeting decisions -> <P>-REVIEW-DECISIONS.md
  fix       fixes             fix/build_fixes.py                     clear fixes -> fixes.json
            descriptions      fix/prep_desc.py                       description batches for sub-agents
            payload           fix/build_payload.py                   import payload + aux file
            syntax-check      fix/syntax_check.py                    server check of every proposed text
  e2e       scenarios         types/<type>/scen.py                   scenario data and checks
            e2e <sub>         e2e/run_e2e.py <sub>                   witness | load | revert | reload-scenarios |
                                                                     before | apply | after
            check             e2e/check.py                           engine check before/after + scenarios -> results.json
            viz               types/<type>/make_viz.py               saved pivot tables for the screenshots
            shots             e2e/shots.py                           screenshots (Playwright)
            probes            types/<type>/probes.py                 probe objects for scenarios (create|query|delete)
            evidence          e2e/evidence.py                        regenerate E2E-TEST.md (--force)
  approve   approval          approve/build_fix_review.py            approval page -> <p>-fix-review.html
            standalone        approve/make_standalone.py             offline approval page
            guide             approve/manual_shots.py                reviewer guide PDF (Playwright)
            export            approve/export_approved.py             import files from the approvals
"""
import os, subprocess, sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPTS, 'lib'))
import adapters, config  # noqa: E402

CORE, TYPE = 'core', 'type'
STEPS = {
    'lint': (TYPE, 'lint.py'), 'syntax': (TYPE, 'syntax.py'), 'gen': (TYPE, 'gen.py'), 'import-data': (TYPE, 'import_data.py'),
    'query': (TYPE, 'query.py'), 'compare': (TYPE, 'compare.py'), 'assemble': (CORE, 'validate/assemble.py'), 'probe': (TYPE, 'probe.py'),
    'topics': (CORE, 'review/build_review.py'), 'dictionary': (TYPE, 'build_dictionary.py'), 'decisions': (CORE, 'review/write_decisions.py'),
    'fixes': (CORE, 'fix/build_fixes.py'), 'descriptions': (CORE, 'fix/prep_desc.py'), 'payload': (CORE, 'fix/build_payload.py'),
    'syntax-check': (CORE, 'fix/syntax_check.py'),
    'scenarios': (TYPE, 'scen.py'), 'e2e': (CORE, 'e2e/run_e2e.py'), 'check': (CORE, 'e2e/check.py'), 'viz': (TYPE, 'make_viz.py'),
    'shots': (CORE, 'e2e/shots.py'), 'probes': (TYPE, 'probes.py'), 'evidence': (CORE, 'e2e/evidence.py'),
    'approval': (CORE, 'approve/build_fix_review.py'), 'standalone': (CORE, 'approve/make_standalone.py'),
    'guide': (CORE, 'approve/manual_shots.py'), 'export': (CORE, 'approve/export_approved.py'),
}


def config_path(args):
    for i, a in enumerate(args):
        if a == '--config' and i + 1 < len(args):
            return args[i + 1]
        if a.startswith('--config='):
            return a.split('=', 1)[1]
    return os.environ.get('PIREVIEW_CONFIG', 'pireview.json')


def script_for(typ, step):
    where, name = STEPS[step]
    if where == CORE:
        return os.path.join(SCRIPTS, name)
    return adapters.type_script(typ, name, required=False)


def run(cmd, env):
    print('$', ' '.join(cmd), flush=True)
    return subprocess.call(cmd, env=env)


def main():
    args = sys.argv[1:]
    if not args or args[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    cpath = config_path(args)
    rest = list(args)
    # allow "run.py --config C step ...": take the config off the front, keep it for the script
    if rest and rest[0] == '--config':
        rest = rest[2:]
    elif rest and rest[0].startswith('--config='):
        rest = rest[1:]
    if not rest:
        print(__doc__)
        return 2
    step, extra = rest[0], rest[1:]
    typ = config.config_type(cpath) if os.path.exists(cpath) else config.DEFAULT_TYPE
    if step == 'steps':
        for k, (where, name) in STEPS.items():
            p = script_for(typ, k)
            print(f'{k:14} {where:5} ' + (os.path.relpath(p, SCRIPTS) if p else f'(not supported for {typ})'))
        return 0
    if step not in STEPS:
        print(f'unknown step {step!r}; steps: {", ".join(STEPS)}', file=sys.stderr)
        return 2
    if not os.path.exists(cpath):
        print(f'no run configuration at {cpath}: pass --config or set PIREVIEW_CONFIG', file=sys.stderr)
        return 2
    env = dict(os.environ, PIREVIEW_CONFIG=os.path.abspath(cpath))
    if '--config' not in extra and not any(a.startswith('--config=') for a in extra):
        extra = ['--config', os.path.abspath(cpath)] + extra
    script = script_for(typ, step)
    if not script:
        print(f'step {step!r} is not supported for type {typ!r} (no {STEPS[step][1]} in scripts/types/{typ}/)', file=sys.stderr)
        return 2
    if step == 'dictionary':
        asm = adapters.type_script(typ, 'assemble_dict.py', required=False)
        b_args, a_args = [], ['--config', os.path.abspath(cpath)]
        it = iter(extra)
        for a in it:
            if a == '--descriptions':
                a_args += [a, next(it)]
            elif a == '--config':
                next(it)
            else:
                b_args.append(a)
        rc = run([sys.executable, script, '--config', os.path.abspath(cpath), *b_args], env)
        if rc or not asm:
            return rc
        return run([sys.executable, asm, *a_args], env)
    return run([sys.executable, script, *extra], env)


if __name__ == '__main__':
    sys.exit(main())
