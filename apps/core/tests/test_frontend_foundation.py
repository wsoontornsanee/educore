"""Tests for Frontend Milestone 0 Foundation: templates, design tokens, and components."""
from pathlib import Path
from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase


class FrontendFoundationTests(SimpleTestCase):
    def test_base_template_renders_properly(self):
        context = {
            'csrf_token': 'dummy-csrf-token-123',
        }
        rendered = render_to_string('base.html', context)
        self.assertIn('<!DOCTYPE html>', rendered)
        self.assertIn('EduCore', rendered)
        self.assertIn('/static/css/app.css', rendered)
        self.assertIn('htmx.org', rendered)
        self.assertIn('Plus+Jakarta+Sans', rendered)
        self.assertIn('IBM+Plex+Sans', rendered)

    def test_badge_component_rendering_and_semantic_colors(self):
        # Hadir status
        rendered_hadir = render_to_string('components/badge.html', {'status': 'HADIR'})
        self.assertIn('HADIR', rendered_hadir)
        self.assertIn('status-badge', rendered_hadir)
        self.assertIn('status-dot', rendered_hadir)
        self.assertIn('var(--color-att-hadir)', rendered_hadir)

        # Alpa (absence) status should use danger red, NEVER brand red (#C8102E)
        rendered_alpa = render_to_string('components/badge.html', {'status': 'ALPA'})
        self.assertIn('ALPA', rendered_alpa)
        self.assertIn('var(--color-att-alpa)', rendered_alpa)
        self.assertNotIn('#C8102E', rendered_alpa)

        # Lunas status
        rendered_lunas = render_to_string('components/badge.html', {'status': 'LUNAS'})
        self.assertIn('LUNAS', rendered_lunas)

    def test_banner_component_5_states(self):
        states = ['loading', 'empty', 'stale', 'offline', 'error']
        for st in states:
            rendered = render_to_string('components/banner.html', {'state': st, 'message': f'Pesan untuk {st}'})
            self.assertIn(f'state-banner-{st}', rendered)
            self.assertIn(f'Pesan untuk {st}', rendered)

    def test_pagination_component(self):
        # With next_url
        rendered_with_next = render_to_string('components/pagination.html', {
            'next_url': '/api/v1/academic/gradebook/?cursor=cD0yMDI2',
            'target_id': 'grade-table',
        })
        self.assertIn('hx-get="/api/v1/academic/gradebook/?cursor=cD0yMDI2"', rendered_with_next)
        self.assertIn('Muat Lebih Banyak', rendered_with_next)
        self.assertIn('hx-target="#grade-table"', rendered_with_next)

        # Without next_url
        rendered_empty = render_to_string('components/pagination.html', {'next_url': None})
        self.assertEqual(rendered_empty.strip(), '')

    def test_icons_component(self):
        rendered_check = render_to_string('components/icons.html', {'icon': 'check', 'size': '24'})
        self.assertIn('<svg', rendered_check)
        self.assertIn('width="24"', rendered_check)

        rendered_clock = render_to_string('components/icons.html', {'icon': 'clock'})
        self.assertIn('<svg', rendered_clock)

    def test_static_css_and_tailwind_config_exist(self):
        css_file = Path(settings.BASE_DIR) / 'frontend' / 'static' / 'css' / 'app.css'
        self.assertTrue(css_file.exists(), f"{css_file} does not exist")
        self.assertGreater(css_file.stat().st_size, 500)

        tailwind_file = Path(settings.BASE_DIR) / 'frontend' / 'tailwind' / 'tailwind.config.js'
        self.assertTrue(tailwind_file.exists(), f"{tailwind_file} does not exist")
        content = tailwind_file.read_text(encoding='utf-8')
        self.assertIn('#C8102E', content)  # Brand red
        self.assertIn('Plus Jakarta Sans', content)
        self.assertIn('IBM Plex Mono', content)
