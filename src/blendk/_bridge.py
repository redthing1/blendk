"""Standalone bridge executed by Blender's bundled Python."""

import contextlib
import fnmatch
import io
import json
import os
import socket
import struct
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy


PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_CHARS = 256 * 1024
_HEADER = struct.Struct(">I")
_BUFFER = bytearray()
_REQUESTS = []
_MUTATED = False
_CLOSING = False


def _send(message):
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise RuntimeError("response exceeds the protocol frame limit")
    _SOCKET.sendall(_HEADER.pack(len(payload)) + payload)


def _receive_exact(size):
    chunks = bytearray()
    while len(chunks) < size:
        chunk = _SOCKET.recv(size - len(chunks))
        if not chunk:
            raise EOFError("supervisor disconnected")
        chunks.extend(chunk)
    return bytes(chunks)


def _receive():
    size = _HEADER.unpack(_receive_exact(_HEADER.size))[0]
    if size == 0 or size > MAX_FRAME_BYTES:
        raise RuntimeError("invalid protocol frame size")
    value = json.loads(_receive_exact(size).decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("protocol frame must be an object")
    return value


def _poll():
    while True:
        try:
            chunk = _SOCKET.recv(65536)
        except BlockingIOError:
            break
        if not chunk:
            raise EOFError("supervisor disconnected")
        _BUFFER.extend(chunk)
        if len(_BUFFER) > MAX_FRAME_BYTES + _HEADER.size:
            raise RuntimeError("protocol buffer exceeds the size limit")

    while len(_BUFFER) >= _HEADER.size:
        size = _HEADER.unpack(_BUFFER[: _HEADER.size])[0]
        if size == 0 or size > MAX_FRAME_BYTES:
            raise RuntimeError("invalid protocol frame size")
        end = _HEADER.size + size
        if len(_BUFFER) < end:
            break
        value = json.loads(bytes(_BUFFER[_HEADER.size : end]).decode("utf-8"))
        del _BUFFER[:end]
        if not isinstance(value, dict):
            raise RuntimeError("protocol frame must be an object")
        _REQUESTS.append(value)


def _dirty():
    reasons = []
    if _MUTATED:
        reasons.append("command")
    if bpy.data.is_dirty:
        reasons.append("blender")
    dirty_images = sorted(image.name for image in bpy.data.images if image.is_dirty)
    if dirty_images:
        reasons.append("images")
    return {
        "dirty": bool(reasons),
        "dirty_reasons": reasons,
        "dirty_images": dirty_images[:20],
        "dirty_images_truncated": len(dirty_images) > 20,
    }


def _status():
    scene = bpy.context.scene
    result = {
        "blender_version": bpy.app.version_string,
        "python_version": sys.version.split()[0],
        "mode": os.environ["BLENDK_MODE"],
        "project": os.environ["BLENDK_PROJECT"],
        "file": bpy.data.filepath or None,
        "scene": scene.name,
        "frame": scene.frame_current,
        "camera": scene.camera.name if scene.camera else None,
        "engine": scene.render.engine,
        "resolution": [scene.render.resolution_x, scene.render.resolution_y],
        "objects": len(bpy.data.objects),
    }
    result.update(_dirty())
    return result


def _object_info(obj):
    materials = []
    data = getattr(obj, "data", None)
    if data is not None and hasattr(data, "materials"):
        materials = [material.name for material in data.materials if material is not None]
    return {
        "name": obj.name,
        "type": obj.type,
        "location": [round(value, 6) for value in obj.location],
        "rotation": [round(value, 6) for value in obj.rotation_euler],
        "scale": [round(value, 6) for value in obj.scale],
        "dimensions": [round(value, 6) for value in obj.dimensions],
        "visible": not obj.hide_viewport,
        "render_visible": not obj.hide_render,
        "parent": obj.parent.name if obj.parent else None,
        "materials": materials,
    }


def _inspect(params):
    pattern = params.get("pattern")
    if pattern is not None and not isinstance(pattern, str):
        raise ValueError("inspect pattern must be a string")
    objects = sorted(bpy.context.scene.objects, key=lambda item: item.name.casefold())
    if pattern:
        objects = [obj for obj in objects if fnmatch.fnmatchcase(obj.name, pattern)]
    limit = 50
    scene = bpy.context.scene
    result = _status()
    result.update(
        {
            "collections": len(bpy.data.collections),
            "materials": len(bpy.data.materials),
            "lights": len(bpy.data.lights),
            "object_count": len(objects),
            "listed": min(len(objects), limit),
            "truncated": len(objects) > limit,
            "object_list": [_object_info(obj) for obj in objects[:limit]],
            "render_percentage": scene.render.resolution_percentage,
            "output_format": scene.render.image_settings.file_format,
            "registered_engines": _registered_engines(scene),
            "graphics": _graphics(),
            "cycles": _cycles(scene),
        }
    )
    return result


def _registered_engines(scene):
    try:
        prop = scene.render.bl_rna.properties["engine"]
        return sorted(item.identifier for item in prop.enum_items)
    except Exception as error:
        return {"error": str(error)}


def _graphics():
    try:
        import gpu

        if bpy.app.background:
            gpu.init()
        return {
            "backend": gpu.platform.backend_type_get(),
            "vendor": gpu.platform.vendor_get(),
            "renderer": gpu.platform.renderer_get(),
        }
    except Exception as error:
        return {"error": str(error)}


def _cycles(scene):
    result = {"scene_device": getattr(getattr(scene, "cycles", None), "device", None)}
    try:
        addon = bpy.context.preferences.addons.get("cycles")
        if addon is None:
            return result
        preferences = addon.preferences
        preferences.get_devices()
        result["backend"] = preferences.compute_device_type
        result["devices"] = [
            {"name": device.name, "type": device.type, "enabled": bool(device.use)}
            for device in preferences.devices
        ]
    except Exception as error:
        result["error"] = str(error)
    return result


def _json_value(value, depth=0):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if depth >= 4:
        return repr(value)[:1000]
    if isinstance(value, (list, tuple)):
        return [_json_value(item, depth + 1) for item in value[:100]]
    if isinstance(value, dict):
        return {
            str(key): _json_value(item, depth + 1)
            for key, item in list(value.items())[:100]
        }
    try:
        return [_json_value(item, depth + 1) for item in list(value)[:100]]
    except (TypeError, AttributeError):
        return repr(value)[:1000]


def _evaluate(params):
    source = params.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("eval requires a nonempty expression")
    namespace = {"bpy": bpy}
    value = eval(compile(source, "<blendk-eval>", "eval"), namespace, namespace)
    return {"value": _json_value(value)}


def _run(params):
    global _MUTATED
    source = params.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("run requires a nonempty script")
    output = io.StringIO()
    errors = io.StringIO()
    helper = SimpleNamespace(
        project=Path(os.environ["BLENDK_PROJECT"]),
        artifacts=Path(os.environ["BLENDK_ARTIFACTS"]),
    )
    namespace = {
        "__name__": "__blendk__",
        "bpy": bpy,
        "blendk": helper,
    }
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        exec(compile(source, "<blendk-run>", "exec"), namespace, namespace)
    _MUTATED = True
    return {
        "stdout": output.getvalue()[:MAX_OUTPUT_CHARS],
        "stderr": errors.getvalue()[:MAX_OUTPUT_CHARS],
        "truncated": output.tell() > MAX_OUTPUT_CHARS or errors.tell() > MAX_OUTPUT_CHARS,
    }


def _visual(params, preview):
    destination = params.get("path")
    if not isinstance(destination, str) or not Path(destination).is_absolute():
        raise ValueError("render destination must be an absolute path")
    scene = bpy.context.scene
    if scene.camera is None:
        raise ValueError("the scene has no active camera")

    camera_name = params.get("camera")
    frame = params.get("frame")
    size = params.get("size")
    original_camera = scene.camera
    original_frame = scene.frame_current
    original = {
        "engine": scene.render.engine,
        "resolution_x": scene.render.resolution_x,
        "resolution_y": scene.render.resolution_y,
        "resolution_percentage": scene.render.resolution_percentage,
        "file_format": scene.render.image_settings.file_format,
        "color_mode": scene.render.image_settings.color_mode,
        "color_depth": scene.render.image_settings.color_depth,
        "film_transparent": scene.render.film_transparent,
    }
    shading = scene.display.shading
    shading_original = {}
    for name in ("light", "color_type", "show_shadows", "show_cavity", "cavity_type"):
        if hasattr(shading, name):
            shading_original[name] = getattr(shading, name)

    try:
        if camera_name is not None:
            if not isinstance(camera_name, str):
                raise ValueError("camera must be an object name")
            camera = bpy.data.objects.get(camera_name)
            if camera is None or camera.type != "CAMERA":
                raise ValueError("camera not found: %s" % camera_name)
            scene.camera = camera
        if frame is not None:
            if not isinstance(frame, int):
                raise ValueError("frame must be an integer")
            scene.frame_set(frame)
        if size is not None:
            if (
                not isinstance(size, list)
                or len(size) != 2
                or not all(isinstance(value, int) and 1 <= value <= 16384 for value in size)
            ):
                raise ValueError("size must contain two integers from 1 to 16384")
            scene.render.resolution_x, scene.render.resolution_y = size
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGBA"
        scene.render.image_settings.color_depth = "8"
        if preview:
            scene.render.engine = "BLENDER_WORKBENCH"
            scene.render.film_transparent = False
            settings = {
                "light": "STUDIO",
                "color_type": "MATERIAL",
                "show_shadows": True,
                "show_cavity": True,
                "cavity_type": "WORLD",
            }
            for name, value in settings.items():
                if hasattr(shading, name):
                    setattr(shading, name, value)

        used = {
            "engine": scene.render.engine,
            "camera": scene.camera.name,
            "frame": scene.frame_current,
            "size": [scene.render.resolution_x, scene.render.resolution_y],
        }
        bpy.ops.render.render()
        image = bpy.data.images.get("Render Result")
        if image is None:
            raise RuntimeError("renderer produced no Render Result")
        image.save_render(destination, scene=scene)
        if not Path(destination).is_file():
            raise RuntimeError("renderer produced no image artifact")
        return {"path": destination, **used}
    finally:
        scene.camera = original_camera
        scene.frame_set(original_frame)
        scene.render.engine = original["engine"]
        scene.render.resolution_x = original["resolution_x"]
        scene.render.resolution_y = original["resolution_y"]
        scene.render.resolution_percentage = original["resolution_percentage"]
        scene.render.image_settings.file_format = original["file_format"]
        scene.render.image_settings.color_mode = original["color_mode"]
        scene.render.image_settings.color_depth = original["color_depth"]
        scene.render.film_transparent = original["film_transparent"]
        for name, value in shading_original.items():
            setattr(shading, name, value)


def _save(params):
    global _MUTATED
    destination = params.get("path")
    if destination is None:
        if not bpy.data.filepath:
            raise ValueError("the scene has no current file; provide a save path")
        bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)
    elif isinstance(destination, str):
        bpy.ops.wm.save_as_mainfile(filepath=destination, check_existing=False)
    else:
        raise ValueError("save path must be a string")
    _MUTATED = False
    return {"path": bpy.data.filepath}


def _dispatch(request):
    global _CLOSING
    method = request.get("method")
    params = request.get("params")
    if not isinstance(params, dict):
        raise ValueError("request params must be an object")
    if method == "status":
        return _status()
    if method == "inspect":
        return _inspect(params)
    if method == "eval":
        return _evaluate(params)
    if method == "run":
        return _run(params)
    if method == "preview":
        return _visual(params, True)
    if method == "render":
        return _visual(params, False)
    if method == "save":
        return _save(params)
    if method == "close":
        _CLOSING = True
        return {"closing": True}
    raise ValueError("unknown bridge method: %s" % method)


def _handle(request):
    request_id = request.get("id")
    try:
        result = _dispatch(request)
        response = {
            "v": PROTOCOL_VERSION,
            "kind": "response",
            "id": request_id,
            "ok": True,
            "result": result,
        }
    except Exception as error:
        response = {
            "v": PROTOCOL_VERSION,
            "kind": "response",
            "id": request_id,
            "ok": False,
            "error": {
                "code": "blender_error",
                "message": str(error) or error.__class__.__name__,
                "details": traceback.format_exc(limit=20)[-16000:],
            },
        }
    _send(response)


def _headless():
    while True:
        request = _receive()
        _handle(request)
        if _CLOSING:
            return


def _headed_tick():
    try:
        _poll()
        if _REQUESTS:
            _SOCKET.setblocking(True)
            try:
                _handle(_REQUESTS.pop(0))
            finally:
                _SOCKET.setblocking(False)
        if _CLOSING:
            bpy.ops.wm.quit_blender()
            return None
        return 0.05
    except EOFError:
        return None
    except Exception:
        traceback.print_exc()
        return 0.25


_SOCKET = socket.create_connection(
    (os.environ["BLENDK_BRIDGE_HOST"], int(os.environ["BLENDK_BRIDGE_PORT"])),
    timeout=15,
)
_send(
    {
        "v": PROTOCOL_VERSION,
        "kind": "hello",
        "role": "bridge",
        "token": os.environ["BLENDK_BRIDGE_TOKEN"],
        "blender_version": bpy.app.version_string,
    }
)
hello = _receive()
if hello.get("ok") is not True:
    raise RuntimeError("blendk supervisor rejected the bridge")

if os.environ["BLENDK_MODE"] == "headless":
    _headless()
else:
    _SOCKET.setblocking(False)
    bpy.app.timers.register(_headed_tick, first_interval=0.05, persistent=True)
