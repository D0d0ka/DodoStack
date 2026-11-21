"""Discover service manifests without importing or starting service applications."""
from dataclasses import dataclass
from importlib import import_module
import json
from pathlib import Path
import re


class ServiceConfigError(ValueError):
    """A service manifest is invalid or conflicts with another service."""


@dataclass(frozen=True)
class Service:
    id: str
    directory: Path
    name: str
    description: str
    entrypoint: str
    logo: Path
    search_aliases: tuple[str, ...]
    admin_label: str | None
    order: int
    commands: dict

    def import_target(self, target):
        module, attribute = target.split(':')
        imported = import_module(f'subsites.{self.directory.name}.{module}')
        try:
            return getattr(imported, attribute)
        except AttributeError:
            raise ServiceConfigError(f'{self.directory / "service.json"}: target {target} does not exist') from None

    def load_app(self):
        return self.import_target(self.entrypoint)


def discover_services(root: Path) -> dict[str, Service]:
    """Read service.json in each immediate subdirectory, ordered for display."""
    services = {}
    commands = {'set-admin', 'set-owner'}
    if not root.is_dir():
        raise ServiceConfigError(f'Service directory does not exist: {root}')
    for directory in sorted(root.iterdir()):
        manifest = directory / 'service.json'
        if not directory.is_dir() or not manifest.is_file():
            continue
        def fail(message):
            raise ServiceConfigError(f'{manifest}: {message}')

        try:
            data = json.loads(manifest.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            fail('could not read a valid JSON object')
        required = {'id', 'name', 'description', 'entrypoint', 'logo'}
        allowed = required | {'search_aliases', 'admin_label', 'order', 'commands'}
        if not isinstance(data, dict) or required - data.keys() or data.keys() - allowed:
            fail('expected id, name, description, entrypoint, logo; optional search_aliases, admin_label, order, commands')
        for key in required:
            if not isinstance(data[key], str) or not data[key].strip():
                fail(f'{key} must be a nonempty string')
        service_id = data['id']
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', service_id) or service_id in {'stack', 'accounts'}:
            fail('id must be a lowercase DNS label other than stack or accounts')
        if service_id in services:
            fail(f'duplicate service id: {service_id}')
        if not directory.name.isidentifier():
            fail('service directory must be a Python package name')
        def validate_target(target):
            if not isinstance(target, str) or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*', target):
                fail('entrypoint and command handlers must use module:attribute inside the service directory')
            module = directory.joinpath(*target.split(':')[0].split('.'))
            if not module.with_suffix('.py').is_file() and not (module / '__init__.py').is_file():
                fail(f'module does not exist for {target}')
        validate_target(data['entrypoint'])
        try:
            logo = (directory / data['logo']).resolve()
        except (OSError, ValueError):
            fail('logo must be a valid relative file path')
        if not logo.is_relative_to(directory.resolve()) or not logo.is_file() or logo.suffix.lower() != '.png':
            fail('logo must reference a PNG file inside the service directory')
        aliases = data.get('search_aliases', [])
        if not isinstance(aliases, list) or any(not isinstance(item, str) or not item.strip() for item in aliases):
            fail('search_aliases must be a list of nonempty strings')
        admin_label = data.get('admin_label')
        if admin_label is not None and (not isinstance(admin_label, str) or not admin_label.strip()):
            fail('admin_label must be a nonempty string or null')
        order = data.get('order', 100)
        if type(order) is not int:
            fail('order must be an integer')
        service_commands = data.get('commands', {})
        if not isinstance(service_commands, dict):
            fail('commands must be an object')
        for command, settings in service_commands.items():
            if not re.fullmatch(r'[a-z][a-z0-9-]*', command) or command in commands:
                fail(f'duplicate or invalid command: {command}')
            if (not isinstance(settings, dict) or set(settings) != {'handler', 'help'}
                    or not isinstance(settings['help'], str) or not settings['help'].strip()):
                fail(f'command {command} requires handler and help strings')
            validate_target(settings['handler'])
            commands.add(command)
        services[service_id] = Service(
            service_id, directory, data['name'], data['description'], data['entrypoint'],
            logo, tuple(aliases), admin_label, order, service_commands,
        )
    return dict(sorted(services.items(), key=lambda pair: (pair[1].order, pair[0])))


SERVICES = discover_services(Path(__file__).resolve().parent.parent / 'subsites')
