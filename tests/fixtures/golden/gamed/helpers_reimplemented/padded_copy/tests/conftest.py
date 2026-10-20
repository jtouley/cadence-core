"""Shared helpers of the sample."""


def make_repo(path):
    path.mkdir(parents=True)
    (path / "README.md").write_text("x\n")
    return path
