from needledrop.units import systemd_unit, udev_rule


def test_systemd_unit_contents():
    u = systemd_unit("vinyl", "/usr/local/bin/needledrop", "/home/vinyl/.config/needledrop/config.yaml")
    for needle in [
        "Restart=always",
        "RestartSec=5",
        "After=network-online.target sound.target",
        "Wants=network-online.target",
        "User=vinyl",
        "SupplementaryGroups=audio",
        "ExecStart=/usr/local/bin/needledrop run "
        "--config /home/vinyl/.config/needledrop/config.yaml",
        "WantedBy=multi-user.target",
    ]:
        assert needle in u


def test_udev_rule():
    r = udev_rule("08bb:2902")
    assert 'ATTR{idVendor}=="08bb"' in r
    assert 'ATTR{idProduct}=="2902"' in r
    assert 'ATTR{power/control}="on"' in r
