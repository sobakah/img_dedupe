"""Opening a group in a viewer, with stand-in programs instead of real apps."""

import pytest

from scripts import viewer


@pytest.fixture
def images(tmp_path):
    paths = []
    for name in ("a.png", "b.jpg"):
        path = tmp_path / name
        path.write_bytes(b"x")
        paths.append(path)
    return paths


class Fakes:
    """Makes more stand-ins; .xdg is the stand-in for the default viewer."""

    def __init__(self, make, xdg):
        self.make, self.xdg = make, xdg

    def __call__(self, *args, **kwargs):
        return self.make(*args, **kwargs)


@pytest.fixture
def only_fakes(fake_command, monkeypatch, tmp_path):
    """Nothing but the stand-ins on PATH, so real viewers on this machine don't interfere
    (a test for "Flatpak isn't installed" must not find the real one)."""
    monkeypatch.setattr(viewer, "_flatpak_state", {})
    xdg = fake_command("xdg-open")
    monkeypatch.setenv("PATH", str(tmp_path / "fakebin"))
    return Fakes(fake_command, xdg)


def test_native_image_compare_is_preferred(only_fakes, images):
    app = only_fakes("imagecompare")
    assert viewer.open_image_viewer(images, "imagecompare") == "Image Compare"
    assert app.calls() == [[str(p) for p in images]]


def test_flatpak_gets_the_files_forwarded(only_fakes, images):
    flatpak = only_fakes("flatpak", script='[ "$1" = info ] && exit 0')
    assert viewer.open_image_viewer(images, "imagecompare") == "Image Compare"
    assert flatpak.calls()[-1] == ["run", "--file-forwarding", "io.github.gimletlove.imagecompare",
                                   "@@", *map(str, images), "@@"]


def test_missing_app_falls_back_to_the_default_viewer(only_fakes, images, capsys):
    only_fakes("flatpak", script='[ "$1" = info ] && exit 1')
    assert viewer.open_image_viewer(images, "identity") == "the default viewer"
    assert "flatpak install flathub org.gnome.gitlab.YaLTeR.Identity" in capsys.readouterr().out


def test_without_flatpak_the_default_viewer_is_used(only_fakes, images):
    assert viewer.open_image_viewer(images, "identity") == "the default viewer"
    # the default viewer is started in the background, once per image: wait for both
    assert sorted(only_fakes.xdg.calls(count=2)) == sorted([[str(p)] for p in images])
