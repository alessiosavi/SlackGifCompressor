from slackgif.cli import main


def test_version(capsys):
    assert main(["--version"]) == 0
    assert "2.0.0" in capsys.readouterr().out


def test_help():
    assert main(["--help"]) == 0
