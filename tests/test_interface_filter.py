from beer_network.interface_filter import INTERFACE_FILTER_ENV, InterfaceFilter


def test_default_filter_includes_everything() -> None:
    f = InterfaceFilter()
    assert f.should_include("eth0")
    assert f.should_include("wlan0")
    assert f.should_include("lo")
    assert f.should_include("docker0")
    assert f.should_include("") is False


def test_no_virtual_excludes_virtual() -> None:
    f = InterfaceFilter.from_environment({INTERFACE_FILTER_ENV: "no-virtual"})
    assert f.should_include("eth0")
    assert f.should_include("wlan0")

    assert not f.should_include("lo")
    assert not f.should_include("docker0")
    assert not f.should_include("br-12345")
    assert not f.should_include("veth123")
    assert not f.should_include("tun0")
    assert not f.should_include("wg0")


def test_exclude_mode() -> None:
    f = InterfaceFilter.from_environment({INTERFACE_FILTER_ENV: "exclude:eth0,^test"})
    assert not f.should_include("eth0")
    assert not f.should_include("test1")
    assert f.should_include("wlan0")


def test_include_mode() -> None:
    f = InterfaceFilter.from_environment({INTERFACE_FILTER_ENV: "include:eth*,^wl"})
    assert f.should_include("eth0")
    assert f.should_include("wlan0")
    assert not f.should_include("lo")


def test_default_is_exclude() -> None:
    f = InterfaceFilter.from_environment({INTERFACE_FILTER_ENV: "eth0,lo"})
    assert not f.should_include("eth0")
    assert not f.should_include("lo")
    assert f.should_include("wlan0")


def test_invalid_regex_skipped() -> None:
    f = InterfaceFilter.from_environment({INTERFACE_FILTER_ENV: "exclude:eth0,["})
    assert not f.should_include("eth0")
    assert f.should_include("wlan0")


def test_filter_interfaces() -> None:
    f = InterfaceFilter.from_environment({INTERFACE_FILTER_ENV: "exclude:lo,docker.*"})
    interfaces = {"eth0": 1, "lo": 2, "docker0": 3}
    filtered = f.filter_interfaces(interfaces)
    assert filtered == {"eth0": 1}
