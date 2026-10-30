"""Focus witness that reads the browser's focus through the DevTools Protocol."""

from dataclasses import dataclass
from typing import Any

from playwright.sync_api import CDPSession, Error

# Follows focus into open shadow roots so the witness reports the element the
# user sees focused, not its shadow host.
_DEEP_ACTIVE_ELEMENT = """(() => {
  let element = document.activeElement;
  while (element && element.shadowRoot && element.shadowRoot.activeElement) {
    element = element.shadowRoot.activeElement;
  }
  return element;
})()"""

_DOCUMENT_TAGS = frozenset({"BODY", "HTML"})


@dataclass(frozen=True)
class Box:
    """Border box of an element in CSS pixels, relative to the viewport.

    Attributes:
        x: Left edge.
        y: Top edge.
        width: Width.
        height: Height.
    """

    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class Focus:
    """The focused element as the browser reports it.

    Attributes:
        backend_node_id: CDP backend node id, stable while the page is loaded.
        tag: Upper-case tag name, or None when nothing is focused.
        dom_id: The element's `id` attribute, if any.
        role: Accessibility role computed by the browser.
        name: Accessible name computed by the browser.
        box: Border box in the viewport, or None when the element has no box.
        document_has_focus: Whether the document itself holds focus. It is
            False after Tab moves past the last element, when focus leaves the
            page for the browser, and stays True when a script blurs the
            focused element.
    """

    backend_node_id: int | None
    tag: str | None
    dom_id: str | None
    role: str | None
    name: str | None
    box: Box | None
    document_has_focus: bool

    @property
    def is_document(self) -> bool:
        """Whether focus sits on the document rather than on an element."""
        return self.tag is None or self.tag in _DOCUMENT_TAGS


class FocusWitness:
    """Reads focus and scroll position of one page through a CDP session."""

    def __init__(self, cdp: CDPSession) -> None:
        """Initializes the witness and enables the Accessibility domain.

        Args:
            cdp: A CDP session attached to the page to observe.
        """
        self._cdp = cdp
        self._cdp.send("Accessibility.enable")

    def focus(self) -> Focus:
        """Returns the element that currently has keyboard focus."""
        document_has_focus: bool = self._cdp.send(
            "Runtime.evaluate",
            {"expression": "document.hasFocus()", "returnByValue": True},
        )["result"]["value"]
        result = self._cdp.send(
            "Runtime.evaluate", {"expression": _DEEP_ACTIVE_ELEMENT}
        )["result"]
        object_id = result.get("objectId")
        if object_id is None:
            return Focus(
                backend_node_id=None,
                tag=None,
                dom_id=None,
                role=None,
                name=None,
                box=None,
                document_has_focus=document_has_focus,
            )
        try:
            node = self._cdp.send("DOM.describeNode", {"objectId": object_id})["node"]
        finally:
            self._cdp.send("Runtime.releaseObject", {"objectId": object_id})
        backend_node_id: int = node["backendNodeId"]
        role, name = self._role_and_name(backend_node_id)
        return Focus(
            backend_node_id=backend_node_id,
            tag=node["nodeName"],
            dom_id=_attribute(node, "id"),
            role=role,
            name=name,
            box=self._box(backend_node_id),
            document_has_focus=document_has_focus,
        )

    def scroll(self) -> tuple[float, float]:
        """Returns the page's scroll offsets (x, y) in CSS pixels."""
        viewport = self._cdp.send("Page.getLayoutMetrics")["cssVisualViewport"]
        return viewport["pageX"], viewport["pageY"]

    def _role_and_name(self, backend_node_id: int) -> tuple[str | None, str | None]:
        nodes = self._cdp.send(
            "Accessibility.getPartialAXTree",
            {"backendNodeId": backend_node_id, "fetchRelatives": False},
        )["nodes"]
        if not nodes:
            return None, None
        return _ax_value(nodes[0], "role"), _ax_value(nodes[0], "name")

    def _box(self, backend_node_id: int) -> Box | None:
        try:
            model = self._cdp.send(
                "DOM.getBoxModel", {"backendNodeId": backend_node_id}
            )["model"]
        except Error:
            # Elements without a layout box (display: none or contents) have
            # no box model; they can still hold focus.
            return None
        xs = model["border"][0::2]
        ys = model["border"][1::2]
        return Box(
            x=min(xs), y=min(ys), width=max(xs) - min(xs), height=max(ys) - min(ys)
        )


def _attribute(node: dict[str, Any], name: str) -> str | None:
    attributes: list[str] = node.get("attributes", [])
    for key, value in zip(attributes[0::2], attributes[1::2], strict=True):
        if key == name:
            return value
    return None


def _ax_value(node: dict[str, Any], field: str) -> str | None:
    value: str | None = node.get(field, {}).get("value")
    return value
