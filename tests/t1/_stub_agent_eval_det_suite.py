"""A tiny, fixed, deterministic stub suite (12 trivial passing cases, 0 failures) used ONLY by
`tests/t1/evidence.py::record_run`'s DETERMINISTIC/default delegation to `dqagent.eval.recorder.run_suite`
(Part F K0 step 3, Decision D5).

Why a stub and not the real `agent_eval` suite: `record_run(s)` (bare defaults) is called by many existing,
already-passing tests (`tests/t1/test_evidence_promotion.py` among them) that depend on the recorded run's exact
shape -- 12 cases, 12 passed, 0 failures -- a pinned fixture value that predates this delegation and that this
change must not disturb (K0_DESIGN.md B13: preserve every caller's behaviour). Running the REAL `agent_eval` suite
here would both change that count (it collects more than 12 cases) and make every DETERMINISTIC `record_run()`
call pay the real suite's full runtime. This stub is still a REAL pytest subprocess run through the SAME
`run_suite` product path the CLI uses (never simulated, never hand-rolled) -- only the suite content run through it
is a small fixed stand-in, exactly the same choice `tests/plugins/_stub_suite.py` already makes for the `explainer`
suite in `tests/plugins/test_eval_recorder.py`. Never collected by the main suite paths (tests/t0, tests/t1 as a
whole, tests/synthetic): referenced only by its explicit path, passed via `run_suite(..., paths=[...])`."""


def test_stub_01(): assert True
def test_stub_02(): assert True
def test_stub_03(): assert True
def test_stub_04(): assert True
def test_stub_05(): assert True
def test_stub_06(): assert True
def test_stub_07(): assert True
def test_stub_08(): assert True
def test_stub_09(): assert True
def test_stub_10(): assert True
def test_stub_11(): assert True
def test_stub_12(): assert True
