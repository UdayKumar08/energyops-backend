from energyops import main


def test_main_prints_greeting(capsys) -> None:
    main()
    assert "energyops" in capsys.readouterr().out
