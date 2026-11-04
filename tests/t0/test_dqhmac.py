from dqagent.evidence import dqhmac


def test_length_prefix_removes_concat_ambiguity():
    k = b"k"
    assert dqhmac.dq_hmac(k, ["AUTH_SNAP", "X1"]) != dqhmac.dq_hmac(k, ["AUTH_SNAPX", "1"])


def test_null_is_not_the_string_null():
    k = b"k"
    assert dqhmac.dq_hmac(k, [None]) != dqhmac.dq_hmac(k, ["NULL"])
    assert dqhmac.dq_hmac(k, [None]) != dqhmac.dq_hmac(k, [""])


def test_is_rfc2104_hmac_sha256():
    import hashlib
    import hmac
    k = b"key"
    msg = dqhmac.canonical(["a", "bc"])
    assert msg == b"1:a2:bc"
    assert dqhmac.dq_hmac(k, ["a", "bc"]) == hmac.new(k, msg, hashlib.sha256).hexdigest()


def test_lanes_and_fold_are_order_independent_and_keyed():
    rows = [["1", "a"], ["2", "b"], ["3", "c"]]
    a = dqhmac.fold(rows, b"k1")
    b = dqhmac.fold(list(reversed(rows)), b"k1")
    c = dqhmac.fold(rows, b"k2")
    assert a == b and a != c
    assert all(v < 2 ** 60 * 3 for v in a[0])


def test_kat_vectors_stable():
    assert len({t for _, t in dqhmac.KAT}) == len(dqhmac.KAT)


def test_duckdb_udf_matches_python_oracle():
    from dqagent.adapters.dataplane.duckdb_dp import DuckDBDataPlane
    dp = DuckDBDataPlane(keys={"K_FP": b"fpkey", "K_EV": b"evkey"})
    dp.execute("CREATE TABLE t(a VARCHAR, b VARCHAR)")
    dp.execute("INSERT INTO t VALUES ('1','x'),('2',NULL),('3','z')")
    tok = dp.hmac_sql("K_FP", ["a", "b"])
    got = dp.fetchall(f"SELECT {dp.lanes_sql(tok)}, count(*) FROM t")[0]
    exp_lanes, exp_n = dqhmac.fold([["1", "x"], ["2", None], ["3", "z"]], b"fpkey")
    assert tuple(int(x) for x in got[:4]) == exp_lanes and got[4] == exp_n
