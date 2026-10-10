"""Regression checks for the trusted SVG geometry compiler (build dependencies only)."""
import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("pathops")
pytest.importorskip("fontTools")

from interface.build_efp_pdf_assets import Geometry, pdf_path


def geometry(markup):
    return Geometry(ET.fromstring('<svg>' + markup + '</svg>'))


def test_nested_placement_transforms_and_use_offsets():
    drawing = geometry('''
        <defs><rect id="tile" width="10" height="8" /></defs>
        <g id="tissue" transform="translate(100 20) scale(2)">
          <g transform="translate(3 4)"><use href="#tile" x="1" y="2" /></g>
        </g>''')
    assert drawing.shape(drawing.by_id['tissue']).bounds == (108, 32, 128, 48)


@pytest.mark.parametrize('mode', ['shape', 'paint', 'mask'])
def test_overlapping_elements_with_opposite_winding_stay_filled(mode):
    drawing = geometry('''
        <g id="pieces" fill="white">
          <path d="M0 0h10v10h-10z" />
          <path d="M5 2v6h10v-6z" />
        </g>
        <mask id="mask" maskUnits="userSpaceOnUse" x="0" y="0" width="20" height="20">
          <use href="#pieces" />
        </mask>
        <rect id="masked" width="20" height="20" mask="url(#mask)" />''')
    if mode == 'paint':
        path = drawing.painted_area(drawing.by_id['pieces'], 'white')
    else:
        path = drawing.shape(drawing.by_id['masked' if mode == 'mask' else 'pieces'])
    # Separate SVG elements paint their overlap regardless of contour direction.
    for point in [(2, 5), (7, 5), (12, 5)]:
        assert path.contains(point)
    assert not path.contains((12, 1))


@pytest.mark.parametrize('mode', ['shape', 'paint'])
def test_evenodd_fill_inherits_through_groups_and_use_with_local_override(mode):
    drawing = geometry('''
        <defs><path id="ring" d="M0 0h10v10h-10z M2 2h6v6h-6z" /></defs>
        <g id="pieces" fill="white" fill-rule="evenodd">
          <g><use href="#ring" /></g>
          <use href="#ring" x="20" fill-rule="nonzero" />
        </g>''')
    node = drawing.by_id['pieces']
    path = drawing.shape(node) if mode == 'shape' else drawing.painted_area(node, 'white')
    assert path.contains((1, 1))
    assert not path.contains((5, 5))
    assert path.contains((25, 5))


def test_binary_mask_honors_paint_order_and_stroke_before_placement():
    drawing = geometry('''
        <defs>
          <g id="art" stroke="black" stroke-width="2">
            <rect x="2" y="2" width="16" height="16" fill="black" />
            <rect x="6" y="6" width="8" height="8" fill="white" />
          </g>
          <mask id="interior" maskUnits="userSpaceOnUse" x="0" y="0" width="20" height="20">
            <rect width="20" height="20" fill="#FFFFFF" />
            <use href="#art" />
          </mask>
        </defs>
        <g id="tissue" transform="translate(100 20) scale(2)">
          <rect width="20" height="20" mask="url(#interior)" />
        </g>
        <g id="outline" transform="translate(100 20) scale(2)"><use href="#art" /></g>''')
    tissue = drawing.shape(drawing.by_id['tissue'])
    ink = drawing.painted_area(drawing.by_id['outline'], 'black')
    for point in [(100.5, 20.5), (120, 40)]:
        assert tissue.contains(point)
        assert not ink.contains(point)
    # The later white rectangle reopens the center; its black stroke stays ink.
    for point in [(104, 24), (112.5, 40)]:
        assert not tissue.contains(point)
        assert ink.contains(point)
    assert ink.bounds == (102, 22, 138, 58)
    assert pdf_path(ink)


def test_ellipses_zero_radius_do_not_leave_strokes():
    drawing = geometry('''
        <g id="ink" fill="black" stroke="black" stroke-width="1">
          <ellipse cx="100" cy="100" rx="0" ry="10" />
          <ellipse cx="200" cy="200" rx="10" ry="0" />
          <ellipse cx="10" cy="20" rx="2" ry="4" />
        </g>''')
    path = drawing.painted_area(drawing.by_id['ink'], 'black')
    assert path.bounds == pytest.approx((7.5, 15.5, 12.5, 24.5), abs=0.002)
    assert path.contains((10, 20))
    assert not path.contains((100, 100))
    assert pdf_path(path)


def test_mask_clips_to_its_explicit_bounds():
    drawing = geometry('''
        <mask id="mask" maskUnits="userSpaceOnUse" x="3" y="4" width="5" height="6">
          <rect width="100" height="100" fill="white" />
        </mask>
        <rect id="tissue" width="100" height="100" mask="url(#mask)" />''')
    assert drawing.shape(drawing.by_id['tissue']).bounds == (3, 4, 8, 10)


def test_unsupported_transforms_and_mask_paint_fail_explicitly():
    drawing = geometry('''
        <rect id="rotated" width="10" height="10" transform="rotate(20)" />
        <g id="gradient" fill="#808080"><rect width="10" height="10" /></g>''')
    with pytest.raises(ValueError, match='Unsupported template transform'):
        drawing.shape(drawing.by_id['rotated'])
    with pytest.raises(ValueError, match='opaque black/white'):
        drawing.painted_area(drawing.by_id['gradient'], 'black')
