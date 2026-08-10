import pytest
from beer_network.interface_filter import InterfaceFilter, INTERFACE_FILTER_ENV

def test_default_filter_includes_everything():
    f = InterfaceFilter()
    assert f.should_include("eth0")
    assert f.should_include("wlan0")
    assert f.should_include("lo")
    assert f.should_include("docker0")
    assert f.should_include("") is False

def test_no_virtual_excludes_virtual():
    f = InterfaceFilter.from_environment({INTERFACE_FILTER_ENV: "no-virtual"})
    assert f.should_include("eth0")
    assert f.should_include("wlan0")
    
    assert not f.should_include("lo")
    assert not f.should_include("docker0")
    assert not f.should_include("br-12345")
    assert not f.should_include("veth123")
    assert not f.should_include("tun0")
    assert not f.should_include("wg0")

def test_exclude_mode():
    f = InterfaceFilter.from_environment({INTERFACE_FILTER_ENV: "exclude:eth0,^test"})
    assert not f.should_include("eth0")
    assert not f.should_include("test1")
    assert f.should_include("wlan0")

def test_include_mode():
    f = InterfaceFilter.from_environment({INTERFACE_FILTER_ENV: "include:eth*,^wl"})
    assert f.should_include("eth0")
    assert f.should_include("wlan0")
    assert not f.should_include("lo")

def test_default_is_exclude():
    f = InterfaceFilter.from_environment({INTERFACE_FILTER_ENV: "eth0,lo"})
    assert not f.should_include("eth0")
    assert not f.should_include("lo")
    assert f.should_include("wlan0")

def test_invalid_regex_skipped():
    f = InterfaceFilter.from_environment({INTERFACE_FILTER_ENV: "exclude:eth0,["})
    assert not f.should_include("eth0")
    assert f.should_include("wlan0")

def test_filter_interfaces():
    f = InterfaceFilter.from_environment({INTERFACE_FILTER_ENV: "exclude:lo,docker.*"})
    interfaces = {"eth0": 1, "lo": 2, "docker0": 3}
    filtered = f.filter_interfaces(interfaces)
    assert filtered == {"eth0": 1}
