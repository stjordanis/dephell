# built-in
from pathlib import Path
from typing import List, Optional

# external
import tomlkit
from dephell_specifier import RangeSpecifier

# app
from ..controllers import DependencyMaker
from ..links import DirLink
from ..models import Constraint, Dependency, RootDependency
from .base import BaseConverter


class PoetryLockConverter(BaseConverter):
    lock = True
    fields = (
        'category', 'description', 'name', 'marker', 'optional',
        'python-versions', 'version', 'dependencies',
    )
    # fields for dependency: python, version, platform

    def can_parse(self, path: Path, content: Optional[str] = None) -> bool:
        if isinstance(path, str):
            path = Path(path)
        if content:
            return ('[[package]]' in content and '[metadata.hashes]' in content)
        else:
            return (path.name in ('pyproject.lock', 'poetry.lock'))

    def loads(self, content) -> RootDependency:
        doc = tomlkit.parse(content)

        deps = []
        root = RootDependency()
        if 'package' in doc:
            for content in doc['package']:
                deps.extend(self._make_deps(root=root, content=content))
        root.attach_dependencies(deps)
        return root

    def dumps(self, reqs, project: RootDependency, content=None) -> str:
        if content:
            doc = tomlkit.parse(content)
        else:
            doc = tomlkit.document()

        doc['package'] = [self._format_req(req=req) for req in reqs]

        doc['metadata'] = {
            # sha256 of tool.poetry section from pyproject.toml
            # 'content-hash': ...,
            'platform': '*',
            'python-versions': '*',
        }

        doc['metadata']['hashes'] = tomlkit.table()
        for req in reqs:
            doc['metadata']['hashes'][req.name] = list(req.hashes or [])

        return tomlkit.dumps(doc)

    # https://github.com/sdispater/poetry/blob/master/poetry.lock
    @classmethod
    def _make_deps(cls, root, content) -> List[Dependency]:
        # get link
        url = None
        if 'source' in content:
            url = content['source']['url']
            if content['source']['type'] == 'git':
                url = 'git+' + url
                if 'reference' in content['source']:
                    url += '@' + content['source']['reference']

        marker = content.get('marker', None)
        if content.get('python-versions', '*') != '*':
            python = RangeSpecifier(content['python-versions']).to_marker('python_version')
            if marker is None:
                marker = python
            else:
                marker = '({}) and {}'.format(marker, python)

        deps = DependencyMaker.from_params(
            raw_name=content['name'],
            description=content['description'],
            constraint=Constraint(root, '==' + content['version']),
            marker=marker,
            url=url,
            editable=False,
        )

        if content.get('category', '') == 'dev':
            for dep in deps:
                dep.envs.add('dev')

        subdeps = []
        for subname, subcontent in content.get('dependencies', dict()).items():
            if isinstance(subcontent, list):
                subcontent = ','.join(set(subcontent))
            if isinstance(subcontent, str):
                subdeps.extend(DependencyMaker.from_params(
                    raw_name=subname,
                    constraint=Constraint(root, '==' + subcontent),
                ))
                continue

            if 'python' in subcontent:
                marker = RangeSpecifier(subcontent['python']).to_marker('python_version')
            else:
                marker = None

            if isinstance(subcontent['version'], list):
                subcontent['version'] = ','.join(set(subcontent['version']))
            subdeps.extend(DependencyMaker.from_params(
                raw_name=subname,
                constraint=Constraint(root, subcontent['version']),
                marker=marker,
            ))
        deps[0].dependencies = tuple(subdeps)

        return deps

    def _format_req(self, req):
        result = tomlkit.table()
        for name, value in req:
            if name in self.fields:
                if isinstance(value, tuple):
                    value = list(value)
                result[name] = value
        result['category'] = 'dev' if 'dev' in req.envs else 'main'
        if 'version' not in result:
            result['version'] = '*'
        result['version'] = result['version'].lstrip('=')
        if req.markers:
            result['marker'] = req.markers

        # add link
        if req.link:
            result['source'] = tomlkit.table()
            if req.git:
                result['source']['type'] = 'git'
            elif isinstance(req.link, DirLink):
                result['source']['type'] = 'directory'
            else:
                result['source']['type'] = 'legacy'
            result['source']['url'] = req.link.short
            if req.rev:
                result['source']['reference'] = req.rev

        # add dependencies
        deps = req.dep.dependencies
        if deps:
            result['dependencies'] = tomlkit.table()
            for dep in deps:
                result['dependencies'][dep.name] = str(dep.constraint)

        return result
