"""The golden corpus and its verdict table are pinned by hash.

A sample or a verdict that changes changes what a gate passes, so it is a product change.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

GOLDEN = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "golden"

GOLDEN_SHA256 = {
    "bad/branch_chain/elif_chain/dispatch.py": "ed20c4c933c6e7e46fbdb8baefdb0da60a45e4b4923a414379e3686e9526cb0f",
    "bad/bugs/bare_except_shell/parse.py": "c86095a49888e0bfe35cbc8f42b1e3256f92e9e3186ed6c09baf89a5a6345869",
    "bad/duplication/renamed_copy/copy.py": "18640c2ba5a3d8f20f590c19303f5f1f46f9c63a3593a15ac0568dfb188c8dd2",
    "bad/duplication/renamed_copy/load.py": "c0d6c9efa3a03d60e33c01eb6a7c5e92b302f6b5683f87df186fc9190aab4bd7",
    "bad/helpers_reimplemented/renamed_copy/scripts/mod.py": "c7287b38db0571a61c9c7fd48876b482bbf9de1de47b4c82465778355d686e33",
    "bad/helpers_reimplemented/renamed_copy/scripts/other.py": "029788b50259123e3f7bdbdae27d970da11f0a8b5040a0166dc885b508605fee",
    "bad/helpers_reimplemented/renamed_copy/tests/conftest.py": "d8c814f61f25c3823bb16cf77e53122957e1f9527f430af67343a11050111ce7",
    "bad/helpers_reimplemented/renamed_copy/tests/test_mod.py": "82a76f692de8795a5df1c6d78af0ef19c267e5a2cbf2c2a395e8c0e6e03ddcd3",
    "bad/helpers_reimplemented/renamed_copy/tests/test_new.py": "d9cd1e6e3f31a4b83027b605af531874cf71bdb5368b2f215a20a2c33765fce0",
    "bad/statements/long/build.py": "c9695fb1a57d79d4a4c517c833a10d034728d455d4917f290d40e636a3e060ac",
    "bad/test_shape/src_layout/pyproject.toml": "a3cc745718951d0e91c0bcbbc86605439575a335b64b96affd94aa878dbd7ece",
    "bad/test_shape/src_layout/src/shapes/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "bad/test_shape/src_layout/src/shapes/size.py": "c7287b38db0571a61c9c7fd48876b482bbf9de1de47b4c82465778355d686e33",
    "bad/test_shape/src_layout/src/shapes/twice.py": "029788b50259123e3f7bdbdae27d970da11f0a8b5040a0166dc885b508605fee",
    "bad/test_shape/src_layout/tests/unit/conftest.py": "d8c814f61f25c3823bb16cf77e53122957e1f9527f430af67343a11050111ce7",
    "bad/test_shape/src_layout/tests/unit/test_new.py": "e7bc39bae7f14d13fa921b5d3394713972f422d0f8e1893bc19601883b70efc8",
    "bad/test_shape/src_layout/tests/unit/test_size.py": "5fdd7c7f006cd81be1d41edc5de6843ee5746591c725601f74e249342120ce64",
    "bad/thin_files/one_test/scripts/mod.py": "c7287b38db0571a61c9c7fd48876b482bbf9de1de47b4c82465778355d686e33",
    "bad/thin_files/one_test/scripts/other.py": "029788b50259123e3f7bdbdae27d970da11f0a8b5040a0166dc885b508605fee",
    "bad/thin_files/one_test/tests/conftest.py": "d8c814f61f25c3823bb16cf77e53122957e1f9527f430af67343a11050111ce7",
    "bad/thin_files/one_test/tests/test_mod.py": "82a76f692de8795a5df1c6d78af0ef19c267e5a2cbf2c2a395e8c0e6e03ddcd3",
    "bad/thin_files/one_test/tests/test_new.py": "aadc9d10c3c3d846916c4e6f49396f3d603dc3f096d9ebc6e50ff09fce6391eb",
    "bad/types/wrong_return/boxes.py": "38e159efe8700a1270aa8ea62b2b16fe265d164d883ce8ee6bcd88f602b9fc99",
    "gamed/branch_chain/interleaved_guards/dispatch.py": "3cd7ebe65ec130c832696f4a243035ed589bf88687c9ecfe20941c5b6450326c",
    "gamed/bugs/targeted_noqa/parse.py": "5e85713aba55e2b0e146b951b90f4ed7f98cec5ca2d56bbb61a9fac74b1dd478",
    "gamed/duplication/interleaved_copy/copy.py": "9e1378046697b5764cd02f02ab2583e7f62517f42c2334fff507bf7e010647fb",
    "gamed/duplication/interleaved_copy/load.py": "5f0b1fa01f10c1afbc2681aee333a65390f18b87a24766a7b0350b2394019c32",
    "gamed/helpers_reimplemented/padded_copy/scripts/mod.py": "c7287b38db0571a61c9c7fd48876b482bbf9de1de47b4c82465778355d686e33",
    "gamed/helpers_reimplemented/padded_copy/scripts/other.py": "029788b50259123e3f7bdbdae27d970da11f0a8b5040a0166dc885b508605fee",
    "gamed/helpers_reimplemented/padded_copy/tests/conftest.py": "d8c814f61f25c3823bb16cf77e53122957e1f9527f430af67343a11050111ce7",
    "gamed/helpers_reimplemented/padded_copy/tests/test_mod.py": "82a76f692de8795a5df1c6d78af0ef19c267e5a2cbf2c2a395e8c0e6e03ddcd3",
    "gamed/helpers_reimplemented/padded_copy/tests/test_new.py": "4c148565013d34e01010443f4e4163666bc1c9381d54a954c0b8cdd99aa6cdd4",
    "gamed/statements/split_chain/build.py": "4cdce440c72784468c8f82ddd2ee489b473fa3a1fbe446951da3f0e80a967164",
    "gamed/thin_files/padded/scripts/mod.py": "c7287b38db0571a61c9c7fd48876b482bbf9de1de47b4c82465778355d686e33",
    "gamed/thin_files/padded/scripts/other.py": "029788b50259123e3f7bdbdae27d970da11f0a8b5040a0166dc885b508605fee",
    "gamed/thin_files/padded/tests/conftest.py": "d8c814f61f25c3823bb16cf77e53122957e1f9527f430af67343a11050111ce7",
    "gamed/thin_files/padded/tests/test_mod.py": "82a76f692de8795a5df1c6d78af0ef19c267e5a2cbf2c2a395e8c0e6e03ddcd3",
    "gamed/thin_files/padded/tests/test_new.py": "3d8bc48fae48bd52fe4fd242c69211b5faf10723323aa90ce3d359a7d0fe4223",
    "gamed/types/type_ignore/boxes.py": "b5e84efb81f6efe9c92d097bb0e447ba2b44f922747e5710cd25773e78395313",
    "good/branch_chain/table_dispatch/dispatch.py": "58dbd1993796a1480f7f0db2cc296d75142a30a7c77d3fe60e2bd7c25540c2ca",
    "good/bugs/narrow_except/parse.py": "5678bdabc00860bf39f84e1864b08b2212ddca77f512d54d691fcc762005cf1d",
    "good/duplication/distinct/load.py": "81cd9af7d091e50853029e20a9855363ccf1fcc84742ad580668d7675b2c08dc",
    "good/duplication/distinct/save.py": "571f25b759a50a1ea31f5800dce4d39ad1436fa3f9b3ebf048bb7ae63b4030ef",
    "good/duplication/parametrize_table/test_table.py": "2273da60e4a5dbffa2fb10c59a318b177ee0ca4695be4dfaa0b717fdd9b4d2d4",
    "good/helpers_reimplemented/imports_helper/scripts/mod.py": "c7287b38db0571a61c9c7fd48876b482bbf9de1de47b4c82465778355d686e33",
    "good/helpers_reimplemented/imports_helper/scripts/other.py": "029788b50259123e3f7bdbdae27d970da11f0a8b5040a0166dc885b508605fee",
    "good/helpers_reimplemented/imports_helper/tests/conftest.py": "d8c814f61f25c3823bb16cf77e53122957e1f9527f430af67343a11050111ce7",
    "good/helpers_reimplemented/imports_helper/tests/test_mod.py": "82a76f692de8795a5df1c6d78af0ef19c267e5a2cbf2c2a395e8c0e6e03ddcd3",
    "good/helpers_reimplemented/imports_helper/tests/test_new.py": "29ffb3ffa5d9708bc474348c6dcab28773a1adca280471d6b26cbcc138205db0",
    "good/statements/short/build.py": "1cc51fcf813b57447b3d0ceba545561f2bede24aee099896563d45cb06032b6a",
    "good/test_shape/src_layout/pyproject.toml": "a3cc745718951d0e91c0bcbbc86605439575a335b64b96affd94aa878dbd7ece",
    "good/test_shape/src_layout/src/shapes/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "good/test_shape/src_layout/src/shapes/size.py": "c7287b38db0571a61c9c7fd48876b482bbf9de1de47b4c82465778355d686e33",
    "good/test_shape/src_layout/src/shapes/twice.py": "029788b50259123e3f7bdbdae27d970da11f0a8b5040a0166dc885b508605fee",
    "good/test_shape/src_layout/tests/unit/conftest.py": "d8c814f61f25c3823bb16cf77e53122957e1f9527f430af67343a11050111ce7",
    "good/test_shape/src_layout/tests/unit/test_new.py": "7346af1f963b5457bcd6b38d3a83d88105e92487f11a901ab4a54bb3b69ee3e2",
    "good/test_shape/src_layout/tests/unit/test_size.py": "5fdd7c7f006cd81be1d41edc5de6843ee5746591c725601f74e249342120ce64",
    "good/thin_files/full_file/scripts/mod.py": "c7287b38db0571a61c9c7fd48876b482bbf9de1de47b4c82465778355d686e33",
    "good/thin_files/full_file/scripts/other.py": "029788b50259123e3f7bdbdae27d970da11f0a8b5040a0166dc885b508605fee",
    "good/thin_files/full_file/tests/conftest.py": "d8c814f61f25c3823bb16cf77e53122957e1f9527f430af67343a11050111ce7",
    "good/thin_files/full_file/tests/test_mod.py": "82a76f692de8795a5df1c6d78af0ef19c267e5a2cbf2c2a395e8c0e6e03ddcd3",
    "good/thin_files/full_file/tests/test_new.py": "f9383e13c367a056c07714d689a6f757754c1e969672a988619a440dc38ee8aa",
    "good/types/declared_return/boxes.py": "4aec28057028c8a22397111537fcbcb0cfda4bc5b9c63939f5e8c1b924c3a636",
    "verdicts.json": "ea8aa5f4f59fbb4579bdb9f2641999ad15e96adeda2023c6221981426723710a",
}


def test_the_golden_corpus_is_unchanged():
    found = {str(p.relative_to(GOLDEN)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(GOLDEN.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}
    assert found == GOLDEN_SHA256, (
        "A golden sample or verdict changed. That is a product change: add a CHANGELOG.md line that names "
        "the sample and the verdict, then refresh GOLDEN_SHA256. A comment refresh alone is not enough."
    )
