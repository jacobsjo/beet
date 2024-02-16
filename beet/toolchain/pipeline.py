__all__ = [
    "GenericPipeline",
    "Task",
    "GenericPlugin",
    "GenericPluginSpec",
    "PluginError",
    "PluginImportError",
]


from dataclasses import dataclass, field
from typing import (
    Any,
    Generator,
    Generic,
    Iterable,
    Iterator,
    List,
    Optional,
    Protocol,
    Set,
    TypeVar,
    Union,
    cast,
)

from beet.core.error import BubbleException, WrappedException
from beet.core.utils import format_obj, import_from_string, pop_traceback

T = TypeVar("T")
ContextType = TypeVar("ContextType", contravariant=True)


class GenericPlugin(Protocol[ContextType]):
    """Protocol for detecting plugins."""

    def __call__(self, ctx: ContextType, /) -> Any: ...


GenericPluginSpec = Union[GenericPlugin[ContextType], str]


class PluginError(WrappedException):
    """Raised when a plugin raises an exception."""

    plugin: Any

    def __init__(self, plugin: Any):
        super().__init__(plugin)
        self.plugin = plugin

    def __str__(self) -> str:
        return f"Plugin {format_obj(self.plugin)} raised an exception."


class PluginImportError(WrappedException):
    """Raised when a plugin couldn't be imported."""

    plugin: Any

    def __init__(self, plugin: Any):
        super().__init__(plugin)
        self.plugin = plugin

    def __str__(self) -> str:
        return f"Couldn't import plugin {format_obj(self.plugin)}."


@dataclass
class Task(Generic[T]):
    """A unit of work generated by the pipeline."""

    plugin: GenericPlugin[T]
    iterator: Optional[Iterator[Any]] = None

    def advance(
        self,
        ctx: T,
        exception: Optional[Exception] = None,
    ) -> Optional["Task[T]"]:
        """Make progress on the task and return it unless no more work is necessary."""
        try:
            if self.iterator is None:
                result = self.plugin(ctx)
                self.iterator = iter(
                    cast(Iterable[Any], result) if isinstance(result, Iterable) else []
                )
            if exception is None:
                next(self.iterator)
            elif isinstance(self.iterator, Generator):
                self.iterator.throw(exception)
            else:
                raise exception
        except StopIteration:
            return None
        except BubbleException:
            raise
        except Exception as exc:
            raise PluginError(self.plugin) from pop_traceback(exc)
        return self


@dataclass
class GenericPipeline(Generic[T]):
    """The plugin execution engine."""

    ctx: T
    default_symbol: str = "beet_default"

    whitelist: Optional[List[str]] = None
    plugins: Set[GenericPlugin[T]] = field(default_factory=set)
    tasks: List[Task[T]] = field(default_factory=list)

    def require(self, *args: GenericPluginSpec[T]):
        """Execute the specified plugin."""
        for spec in args:
            plugin = self.resolve(spec)
            if plugin in self.plugins:
                continue

            self.plugins.add(plugin)

            if remaining_work := Task(plugin).advance(self.ctx):
                self.tasks.append(remaining_work)

    def resolve(self, spec: GenericPluginSpec[T]) -> GenericPlugin[T]:
        """Return the imported plugin if the argument is a dotted path."""
        try:
            return (
                import_from_string(
                    dotted_path=spec,
                    default_member=self.default_symbol,
                    whitelist=self.whitelist,
                )
                if isinstance(spec, str)
                else spec
            )
        except BubbleException:
            raise
        except Exception as exc:
            raise PluginImportError(spec) from exc

    def run(self, specs: Iterable[GenericPluginSpec[T]] = ()):
        """Run the specified plugins."""
        try:
            self.require(*specs)
        except Exception as exc:
            exception = exc
        else:
            exception = None

        while self.tasks:
            try:
                if remaining_work := self.tasks.pop().advance(self.ctx, exception):
                    self.tasks.append(remaining_work)
            except Exception as exc:
                exception = exc
            else:
                exception = None

        if exception is not None:
            raise exception
