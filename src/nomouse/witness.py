"""Focus witness that reads the browser's focus through the DevTools Protocol."""

import re
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import CDPSession, Error

# Follows focus into open shadow roots and same-origin frames so the witness
# reports the element the user sees focused, not its shadow host or <iframe>.
# A frame whose own document has nothing focused (a scrollable frame is a Tab
# stop) is reported as the <iframe>. Cross-origin frames never load: the
# request guard aborts them.
_DEEP_ACTIVE_ELEMENT = """(() => {
  let element = document.activeElement;
  while (element) {
    const frameDocument = element.contentDocument;
    const inner = element.shadowRoot?.activeElement
      ?? (frameDocument?.activeElement === frameDocument?.body
        ? null : frameDocument?.activeElement);
    if (!inner) {
      break;
    }
    element = inner;
  }
  return element;
})()"""

# Called on the focused element: samples a 3 by 3 grid over its box with
# elementFromPoint, in the element's own document or shadow root. A point is
# covered when the topmost element there is neither the focused element nor
# inside it. Like any hit test, it ignores content with pointer-events: none
# and, for an element in a frame, anything the parent page draws over it.
_COVER_SAMPLES = """function () {
  const view = this.ownerDocument.defaultView;
  const root = this.getRootNode();
  const box = this.getBoundingClientRect();
  let sampled = 0;
  let covered = 0;
  if (box.width === 0 || box.height === 0) {
    return {sampled, covered};
  }
  for (const fy of [1 / 6, 1 / 2, 5 / 6]) {
    for (const fx of [1 / 6, 1 / 2, 5 / 6]) {
      const x = box.left + box.width * fx;
      const y = box.top + box.height * fy;
      if (x < 0 || y < 0 || x >= view.innerWidth || y >= view.innerHeight) {
        continue;
      }
      sampled += 1;
      const hit = root.elementFromPoint(x, y);
      if (!hit || !this.contains(hit)) {
        covered += 1;
      }
    }
  }
  return {sampled, covered};
}"""

_ASCII_WHITESPACE = re.compile(r"[ \t\n\f\r]+")

_DOCUMENT_TAGS = frozenset({"BODY", "HTML"})
_ELEMENT_NODE = 1
_CONTROL_TAGS = frozenset({"BUTTON", "INPUT", "SELECT", "TEXTAREA"})
_LINK_TAGS = frozenset({"A", "AREA"})


@dataclass(frozen=True)
class Box:
    """Border box of an element in CSS pixels.

    Each field holding a box says which frame it is relative to.

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
class Cover:
    """What lies on top of the focused element, sampled over its box.

    Attributes:
        sampled: Grid points inside the viewport, out of 9.
        covered: Sampled points where the topmost element is neither the
            focused element nor inside it.
    """

    sampled: int
    covered: int


@dataclass(frozen=True)
class Target:
    """An element a keyboard user should be able to reach.

    Attributes:
        backend_node_id: CDP backend node id, stable while the page is loaded.
        tag: Upper-case tag name.
        dom_id: The element's `id` attribute, if any.
        box: Border box in CSS pixels, relative to the top-left corner of its
            document rather than the viewport.
        reached_by: Backend node ids whose landing reaches the target: the
            target first, then, for a click-only target, its descendant
            elements (a browser may focus a scroller or media element inside
            it) and, for a radio, the other radios of its group.
    """

    backend_node_id: int
    tag: str
    dom_id: str | None
    box: Box
    reached_by: list[int]


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
        cover: What covers the element, sampled on the same element object
            the other fields describe; None when focus is on the document.
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
    cover: Cover | None
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
                cover=None,
                document_has_focus=document_has_focus,
            )
        try:
            node = self._cdp.send("DOM.describeNode", {"objectId": object_id})["node"]
            cover = (
                None if node["nodeName"] in _DOCUMENT_TAGS else self._cover(object_id)
            )
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
            cover=cover,
            document_has_focus=document_has_focus,
        )

    def rendered(self) -> frozenset[int]:
        """Returns the backend node ids of the elements the page renders.

        An element is rendered when it has a layout box and its computed
        visibility is `visible`; elements in same-origin frames are included.
        """
        snapshot = self._cdp.send(
            "DOMSnapshot.captureSnapshot", {"computedStyles": ["visibility"]}
        )
        strings: list[str] = snapshot["strings"]
        rendered: set[int] = set()
        for document in snapshot["documents"]:
            backend_ids = document["nodes"]["backendNodeId"]
            rendered.update(
                backend_ids[index] for index in _rendered_elements(document, strings)
            )
        return frozenset(rendered)

    def targets(self) -> list[Target]:
        """Returns the rendered elements a keyboard user should reach.

        Focusable elements are targets: links with `href`, enabled form
        controls, `<summary>`, and elements with `tabindex` of 0 or more or
        `contenteditable`. An element that only responds to clicks (the
        browser reports a click, mousedown or mouseup listener) is a target
        when no ancestor is focusable and no descendant is focusable or
        clickable, so a listener delegated from an app root does not hide the
        fake buttons inside it. Labels are never targets,
        since clicking one activates its control, and neither is anything
        under a disabled `<fieldset>` or an `inert` element. Content made
        inert by a modal dialog and nodes re-rendered during the walk are
        not accounted for. Only elements with a non-empty box count.

        Returns:
            The targets in document order.
        """
        snapshot = self._cdp.send(
            "DOMSnapshot.captureSnapshot", {"computedStyles": ["visibility"]}
        )
        strings: list[str] = snapshot["strings"]
        targets: list[Target] = []
        for document in snapshot["documents"]:
            nodes = document["nodes"]
            parents: list[int] = nodes["parentIndex"]
            backend_ids: list[int] = nodes["backendNodeId"]
            elements = [
                index
                for index, node_type in enumerate(nodes["nodeType"])
                if node_type == _ELEMENT_NODE
            ]
            tags = {index: strings[nodes["nodeName"][index]] for index in elements}
            attributes = {
                index: _snapshot_attributes(strings, nodes["attributes"][index])
                for index in elements
            }
            ancestors = {index: _ancestor_indices(parents, index) for index in elements}
            disabled = {
                index
                for index in elements
                if "inert" in attributes[index]
                or (tags[index] == "FIELDSET" and "disabled" in attributes[index])
            }
            focusable = {
                index
                for index in elements
                if _is_focusable(tags[index], attributes[index])
            }
            clickable = set(nodes.get("isClickable", {}).get("index", [])) - focusable
            contains_interactive = {
                ancestor
                for index in focusable | clickable
                for ancestor in ancestors.get(index, [])
            }
            radio_groups: dict[tuple[int, str], list[int]] = {}
            for index in elements:
                group = _radio_group(index, tags, attributes, ancestors)
                if group is not None:
                    radio_groups.setdefault(group, []).append(index)
            for index, (x, y, width, height) in sorted(
                _rendered_elements(document, strings).items()
            ):
                if index in focusable:
                    group = _radio_group(index, tags, attributes, ancestors)
                    mates = radio_groups[group] if group is not None else []
                    reached_by = [index] + [mate for mate in mates if mate != index]
                elif (
                    index in clickable
                    and tags[index] != "LABEL"
                    and index not in contains_interactive
                    and focusable.isdisjoint(ancestors[index])
                ):
                    reached_by = [index] + [
                        other for other in elements if index in ancestors[other]
                    ]
                else:
                    continue
                if (
                    width <= 0
                    or height <= 0
                    or not disabled.isdisjoint([index, *ancestors[index]])
                ):
                    continue
                targets.append(
                    Target(
                        backend_node_id=backend_ids[index],
                        tag=tags[index],
                        dom_id=attributes[index].get("id"),
                        box=Box(x=x, y=y, width=width, height=height),
                        reached_by=[backend_ids[node] for node in reached_by],
                    )
                )
        return targets

    def scroll(self) -> tuple[float, float]:
        """Returns the page's scroll offsets (x, y) in CSS pixels."""
        viewport = self._cdp.send("Page.getLayoutMetrics")["cssVisualViewport"]
        return viewport["pageX"], viewport["pageY"]

    def _cover(self, object_id: str) -> Cover:
        samples: dict[str, int] = self._cdp.send(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": _COVER_SAMPLES,
                "returnByValue": True,
            },
        )["result"]["value"]
        return Cover(**samples)

    def _role_and_name(self, backend_node_id: int) -> tuple[str | None, str | None]:
        nodes = self._cdp.send(
            "Accessibility.getPartialAXTree",
            {"backendNodeId": backend_node_id, "fetchRelatives": False},
        )["nodes"]
        if not nodes:
            return None, None
        role = _ax_value(nodes[0], "role")
        name = _ax_value(nodes[0], "name")
        if name is not None:
            # Chromium builds differ in trimming names (accname trims and
            # collapses whitespace); normalizing keeps records comparable.
            name = _ASCII_WHITESPACE.sub(" ", name).strip(" ")
        return role, name

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


def _rendered_elements(
    document: dict[str, Any], strings: list[str]
) -> dict[int, list[float]]:
    """Maps snapshot node indices of rendered elements to their bounds.

    An element is rendered when it has a layout box and its computed
    visibility is `visible`.
    """
    node_types: list[int] = document["nodes"]["nodeType"]
    layout = document["layout"]
    return {
        index: bounds
        for index, styles, bounds in zip(
            layout["nodeIndex"], layout["styles"], layout["bounds"], strict=True
        )
        if node_types[index] == _ELEMENT_NODE and strings[styles[0]] == "visible"
    }


def _snapshot_attributes(strings: list[str], pairs: list[int]) -> dict[str, str]:
    return {
        strings[key]: strings[value]
        for key, value in zip(pairs[0::2], pairs[1::2], strict=True)
    }


def _ancestor_indices(parents: list[int], index: int) -> list[int]:
    ancestors = []
    parent = parents[index]
    while parent >= 0:
        ancestors.append(parent)
        parent = parents[parent]
    return ancestors


def _radio_group(
    index: int,
    tags: dict[int, str],
    attributes: dict[int, dict[str, str]],
    ancestors: dict[int, list[int]],
) -> tuple[int, str] | None:
    """Returns (form index or -1, name) for a named radio, else None."""
    if (
        tags[index] != "INPUT"
        or attributes[index].get("type", "").lower() != "radio"
        or not attributes[index].get("name")
    ):
        return None
    form = next((a for a in ancestors[index] if tags.get(a) == "FORM"), -1)
    return form, attributes[index]["name"]


def _is_focusable(tag: str, attributes: dict[str, str]) -> bool:
    if tag in _LINK_TAGS and "href" in attributes:
        return True
    if tag in _CONTROL_TAGS:
        return "disabled" not in attributes
    if tag == "SUMMARY":
        return True
    if attributes.get("contenteditable", "false").lower() != "false":
        return True
    try:
        return int(attributes.get("tabindex", "-1")) >= 0
    except ValueError:
        return False


def _ax_value(node: dict[str, Any], field: str) -> str | None:
    value: str | None = node.get(field, {}).get("value")
    return value
