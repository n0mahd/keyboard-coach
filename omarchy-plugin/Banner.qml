import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Ui

Item {
  id: root

  property bool opened: false
  property string message: ""
  property int duration: 2000
  readonly property int padX: Style.space(10)
  readonly property int padY: Style.space(6)
  readonly property int contentSpacing: Style.space(6)
  readonly property int maxTextWidth: Style.space(420)

  function show(payloadJson) {
    try {
      var payload = JSON.parse(payloadJson || "{}")
      message = String(payload.message || "")
      duration = Math.min(2000, Math.max(500, Number(payload.duration || 2000)))
      opened = message.length > 0
      hideTimer.restart()
    } catch (error) {}
  }

  function close() { opened = false }

  Timer {
    id: hideTimer
    interval: root.duration
    repeat: false
    onTriggered: root.opened = false
  }

  TextMetrics {
    id: textMetrics
    font.family: Style.font.family
    font.bold: true
    font.pixelSize: Style.font.bodySmall
    text: root.message
  }

  TextMetrics {
    id: iconMetrics
    font.family: Style.font.family
    font.pixelSize: Style.font.body
    text: "󰌌"
  }

  IpcHandler {
    target: "keyboard-coach"
    function show(payloadJson: string): string { root.show(payloadJson); return "ok" }
    function close(): string { root.close(); return "ok" }
    function ping(): string { return "ok" }
  }

  PanelWindow {
    visible: root.opened
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    WlrLayershell.namespace: "keyboard-coach"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    exclusionMode: ExclusionMode.Ignore
    mask: Region {}

    BorderSurface {
      width: Math.min(root.maxTextWidth, Math.ceil(textMetrics.advanceWidth))
             + Math.ceil(iconMetrics.advanceWidth) + root.contentSpacing
             + root.padX * 2 + borderLeft + borderRight
      height: Math.max(Style.font.body, Style.font.bodySmall)
              + root.padY * 2 + borderTop + borderBottom
      anchors.horizontalCenter: parent.horizontalCenter
      anchors.top: parent.top
      anchors.topMargin: Style.space(38)
      color: Util.alpha(Color.background, 0.96)
      borderSpec: Border.surfaceSpec("popups", "border", Color.accent, Math.max(1, Style.space(2)))
      radius: Style.cornerRadius

      Row {
        anchors.fill: parent
        anchors.leftMargin: parent.borderLeft + root.padX
        anchors.rightMargin: parent.borderRight + root.padX
        anchors.topMargin: parent.borderTop + root.padY
        anchors.bottomMargin: parent.borderBottom + root.padY
        spacing: root.contentSpacing

        Text {
          width: Math.ceil(iconMetrics.advanceWidth)
          height: parent.height
          text: "󰌌"
          textFormat: Text.PlainText
          color: Color.accent
          font.family: Style.font.family
          font.pixelSize: Style.font.body
          verticalAlignment: Text.AlignVCenter
        }

        Text {
          width: Math.min(root.maxTextWidth, Math.ceil(textMetrics.advanceWidth))
          height: parent.height
          text: root.message
          textFormat: Text.PlainText
          color: Color.foreground
          font: textMetrics.font
          verticalAlignment: Text.AlignVCenter
          elide: Text.ElideRight
          maximumLineCount: 1
        }
      }
    }
  }
}
