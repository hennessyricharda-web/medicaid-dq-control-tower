"""A tiny, fixed stub suite (1 pass, 1 fail) used only to prove the recorder's DB-write mechanics without waiting
for the real explainer suite to run. Never collected by the main suite paths (tests/t0, tests/t1, tests/synthetic):
it is referenced only by its explicit path in tests/plugins/test_eval_recorder.py's `paths=` override."""


def test_stub_pass():
    assert True


def test_stub_fail():
    assert False, "deliberately failing: proves the recorder records passed < cases honestly"
