# Code created by Siddharth Ahuja: www.github.com/ahujasid © 2025
# Stripped for sins2-overhaul: PolyHaven, Hyper3D, Rodin, Hunyuan3D, telemetry removed.

import bpy
import mathutils
import json
import threading
import socket
import time
import requests
import tempfile
import traceback
import os
import shutil
import zipfile
from bpy.props import IntProperty, BoolProperty, StringProperty
import io
from contextlib import redirect_stdout, suppress

bl_info = {
    "name": "Blender MCP",
    "author": "BlenderMCP",
    "version": (1, 2),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > BlenderMCP",
    "description": "Connect Blender to Claude via MCP",
    "category": "Interface",
}


class BlenderMCPServer:
    # Persistent namespace for functions/modules that survive across execute_code calls
    _persistent_ns = {}
    _helpers_loaded = False

    # Auto-load helper scripts on first execute_code call (paths checked in order)
    AUTOLOAD_SCRIPTS = [
        "C:/Users/gabes/AppData/Local/sins2/blender_mcp_helpers.py",
    ]

    def __init__(self, host='0.0.0.0', port=9876):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.server_thread = None

    def start(self):
        if self.running:
            print("Server is already running")
            return

        self.running = True

        try:
            # Create socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)

            # Start server thread
            self.server_thread = threading.Thread(target=self._server_loop)
            self.server_thread.daemon = True
            self.server_thread.start()

            print(f"BlenderMCP server started on {self.host}:{self.port}")
        except Exception as e:
            print(f"Failed to start server: {str(e)}")
            self.stop()

    def stop(self):
        self.running = False

        # Close socket
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None

        # Wait for thread to finish
        if self.server_thread:
            try:
                if self.server_thread.is_alive():
                    self.server_thread.join(timeout=1.0)
            except:
                pass
            self.server_thread = None

        print("BlenderMCP server stopped")

    def _server_loop(self):
        """Main server loop in a separate thread"""
        print("Server thread started")
        self.socket.settimeout(1.0)  # Timeout to allow for stopping

        while self.running:
            try:
                # Accept new connection
                try:
                    client, address = self.socket.accept()
                    print(f"Connected to client: {address}")

                    # Handle client in a separate thread
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client,)
                    )
                    client_thread.daemon = True
                    client_thread.start()
                except socket.timeout:
                    # Just check running condition
                    continue
                except Exception as e:
                    print(f"Error accepting connection: {str(e)}")
                    time.sleep(0.5)
            except Exception as e:
                print(f"Error in server loop: {str(e)}")
                if not self.running:
                    break
                time.sleep(0.5)

        print("Server thread stopped")

    def _handle_client(self, client):
        """Handle connected client"""
        print("Client handler started")
        client.settimeout(None)  # No timeout
        buffer = b''

        try:
            while self.running:
                # Receive data
                try:
                    data = client.recv(8192)
                    if not data:
                        print("Client disconnected")
                        break

                    buffer += data
                    try:
                        # Try to parse command
                        command = json.loads(buffer.decode('utf-8'))
                        buffer = b''

                        # Execute command in Blender's main thread
                        def execute_wrapper():
                            try:
                                response = self.execute_command(command)
                                response_json = json.dumps(response)
                                try:
                                    client.sendall(response_json.encode('utf-8'))
                                except:
                                    print("Failed to send response - client disconnected")
                            except Exception as e:
                                print(f"Error executing command: {str(e)}")
                                traceback.print_exc()
                                try:
                                    error_response = {
                                        "status": "error",
                                        "message": str(e)
                                    }
                                    client.sendall(json.dumps(error_response).encode('utf-8'))
                                except:
                                    pass
                            return None

                        # Schedule execution in main thread
                        bpy.app.timers.register(execute_wrapper, first_interval=0.0)
                    except json.JSONDecodeError:
                        # Incomplete data, wait for more
                        pass
                except Exception as e:
                    print(f"Error receiving data: {str(e)}")
                    break
        except Exception as e:
            print(f"Error in client handler: {str(e)}")
        finally:
            try:
                client.close()
            except:
                pass
            print("Client handler stopped")

    def execute_command(self, command):
        """Execute a command in the main Blender thread"""
        try:
            return self._execute_command_internal(command)

        except Exception as e:
            print(f"Error executing command: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def _execute_command_internal(self, command):
        """Internal command execution with proper context"""
        cmd_type = command.get("type")
        params = command.get("params", {})

        # Base handlers that are always available
        handlers = {
            "get_scene_info": self.get_scene_info,
            "get_object_info": self.get_object_info,
            "get_viewport_screenshot": self.get_viewport_screenshot,
            "execute_code": self.execute_code,
            "get_sketchfab_status": self.get_sketchfab_status,
            "navigate_viewport": self.navigate_viewport,
            "render_views": self.render_views,
            "get_mesh_stats": self.get_mesh_stats,
            "import_sins2_mesh": self.import_sins2_mesh,
        }

        # Add Sketchfab handlers only if enabled
        if bpy.context.scene.blendermcp_use_sketchfab:
            sketchfab_handlers = {
                "search_sketchfab_models": self.search_sketchfab_models,
                "get_sketchfab_model_preview": self.get_sketchfab_model_preview,
                "download_sketchfab_model": self.download_sketchfab_model,
            }
            handlers.update(sketchfab_handlers)

        handler = handlers.get(cmd_type)
        if handler:
            try:
                print(f"Executing handler for {cmd_type}")
                result = handler(**params)
                print(f"Handler execution complete")
                return {"status": "success", "result": result}
            except Exception as e:
                print(f"Error in handler: {str(e)}")
                traceback.print_exc()
                return {"status": "error", "message": str(e)}
        else:
            return {"status": "error", "message": f"Unknown command type: {cmd_type}"}

    def get_scene_info(self):
        """Get information about the current Blender scene"""
        try:
            print("Getting scene info...")
            scene_info = {
                "name": bpy.context.scene.name,
                "object_count": len(bpy.context.scene.objects),
                "objects": [],
                "materials_count": len(bpy.data.materials),
            }

            # Collect minimal object information (limit to first 10 objects)
            for i, obj in enumerate(bpy.context.scene.objects):
                if i >= 10:
                    break

                obj_info = {
                    "name": obj.name,
                    "type": obj.type,
                    # Only include basic location data
                    "location": [round(float(obj.location.x), 2),
                                round(float(obj.location.y), 2),
                                round(float(obj.location.z), 2)],
                }
                scene_info["objects"].append(obj_info)

            print(f"Scene info collected: {len(scene_info['objects'])} objects")
            return scene_info
        except Exception as e:
            print(f"Error in get_scene_info: {str(e)}")
            traceback.print_exc()
            return {"error": str(e)}

    @staticmethod
    def _get_aabb(obj):
        """ Returns the world-space axis-aligned bounding box (AABB) of an object. """
        if obj.type != 'MESH':
            raise TypeError("Object must be a mesh")

        # Get the bounding box corners in local space
        local_bbox_corners = [mathutils.Vector(corner) for corner in obj.bound_box]

        # Convert to world coordinates
        world_bbox_corners = [obj.matrix_world @ corner for corner in local_bbox_corners]

        # Compute axis-aligned min/max coordinates
        min_corner = mathutils.Vector(map(min, zip(*world_bbox_corners)))
        max_corner = mathutils.Vector(map(max, zip(*world_bbox_corners)))

        return [
            [*min_corner], [*max_corner]
        ]

    def get_object_info(self, name):
        """Get detailed information about a specific object"""
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")

        # Basic object info
        obj_info = {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
            "materials": [],
        }

        if obj.type == "MESH":
            bounding_box = self._get_aabb(obj)
            obj_info["world_bounding_box"] = bounding_box

        # Add material slots
        for slot in obj.material_slots:
            if slot.material:
                obj_info["materials"].append(slot.material.name)

        # Add mesh data if applicable
        if obj.type == 'MESH' and obj.data:
            mesh = obj.data
            obj_info["mesh"] = {
                "vertices": len(mesh.vertices),
                "edges": len(mesh.edges),
                "polygons": len(mesh.polygons),
            }

        return obj_info

    def get_viewport_screenshot(self, max_size=800, filepath=None, format="png"):
        """
        Capture a screenshot of the current 3D viewport.

        Parameters:
        - max_size: Maximum size in pixels for the largest dimension of the image
        - filepath: Optional path hint (ignored; addon uses its own temp path)
        - format: Image format (png, jpg, etc.)

        Returns base64-encoded image data to avoid cross-OS filesystem issues.
        """
        import tempfile
        import base64

        try:
            # Find the active 3D viewport
            area = None
            for a in bpy.context.screen.areas:
                if a.type == 'VIEW_3D':
                    area = a
                    break

            if not area:
                return {"error": "No 3D viewport found"}

            # Use a local Windows temp file (addon runs on Windows)
            temp_path = os.path.join(tempfile.gettempdir(), "blender_mcp_screenshot.png")

            # Use OpenGL viewport render instead of screenshot_area
            # screenshot_area captures raw screen pixels which are often black
            # when the viewport isn't actively composited (common with MCP).
            # render.opengl forces a proper viewport render.
            scene = bpy.context.scene
            old_filepath = scene.render.filepath
            old_format = scene.render.image_settings.file_format
            old_res_x = scene.render.resolution_x
            old_res_y = scene.render.resolution_y

            # Set render resolution based on viewport aspect ratio
            region = None
            for r in area.regions:
                if r.type == 'WINDOW':
                    region = r
                    break
            if region:
                aspect = region.width / max(region.height, 1)
                if aspect >= 1:
                    scene.render.resolution_x = max_size
                    scene.render.resolution_y = int(max_size / aspect)
                else:
                    scene.render.resolution_y = max_size
                    scene.render.resolution_x = int(max_size * aspect)
            else:
                scene.render.resolution_x = max_size
                scene.render.resolution_y = max_size

            scene.render.filepath = temp_path
            scene.render.image_settings.file_format = 'PNG'

            # OpenGL render from the 3D viewport camera angle
            with bpy.context.temp_override(area=area):
                bpy.ops.render.opengl(write_still=True)

            # Restore original settings
            scene.render.filepath = old_filepath
            scene.render.image_settings.file_format = old_format
            scene.render.resolution_x = old_res_x
            scene.render.resolution_y = old_res_y

            if not os.path.exists(temp_path):
                return {"error": "Screenshot file was not created on Blender side"}

            # Load to get dimensions
            img = bpy.data.images.load(temp_path)
            width, height = img.size
            bpy.data.images.remove(img)

            # Read the file and encode as base64
            with open(temp_path, 'rb') as f:
                image_b64 = base64.b64encode(f.read()).decode('ascii')

            # Clean up temp file
            try:
                os.remove(temp_path)
            except OSError:
                pass

            return {
                "success": True,
                "width": width,
                "height": height,
                "image_b64": image_b64,
            }

        except Exception as e:
            return {"error": str(e)}

    def _load_autoload_scripts(self):
        """Load helper scripts into persistent namespace on first call."""
        if BlenderMCPServer._helpers_loaded:
            return
        BlenderMCPServer._helpers_loaded = True
        for script_path in self.AUTOLOAD_SCRIPTS:
            if os.path.exists(script_path):
                try:
                    ns = {"bpy": bpy}
                    with open(script_path, 'r') as f:
                        exec(f.read(), ns)
                    # Persist all callable objects and non-dunder names
                    for k, v in ns.items():
                        if not k.startswith('_') and k != 'bpy':
                            BlenderMCPServer._persistent_ns[k] = v
                    print(f"[BlenderMCP] Auto-loaded: {script_path}")
                except Exception as e:
                    print(f"[BlenderMCP] Failed to auto-load {script_path}: {e}")

    def execute_code(self, code):
        """Execute arbitrary Blender Python code"""
        # This is powerful but potentially dangerous - use with caution
        try:
            # Auto-load helper scripts on first call
            self._load_autoload_scripts()

            # Create namespace with bpy + persistent helpers
            namespace = {"bpy": bpy}
            namespace.update(BlenderMCPServer._persistent_ns)

            # Capture stdout during execution, and return it as result
            capture_buffer = io.StringIO()
            with redirect_stdout(capture_buffer):
                exec(code, namespace)

            # Persist any new callables defined by user code
            for k, v in namespace.items():
                if callable(v) and not k.startswith('_') and k != 'bpy':
                    BlenderMCPServer._persistent_ns[k] = v

            captured_output = capture_buffer.getvalue()
            return {"executed": True, "result": captured_output}
        except Exception as e:
            raise Exception(f"Code execution error: {str(e)}")

    def navigate_viewport(self, target=None, location=None, distance=None):
        """Navigate the 3D viewport to frame an object or look at a point."""
        try:
            area = None
            for a in bpy.context.screen.areas:
                if a.type == 'VIEW_3D':
                    area = a
                    break
            if not area:
                return {"error": "No 3D viewport found"}

            r3d = area.spaces[0].region_3d

            if target:
                obj = bpy.data.objects.get(target)
                if not obj:
                    return {"error": f"Object '{target}' not found"}

                # Calculate bounding box center and size in world space
                bbox_corners = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
                center = sum(bbox_corners, mathutils.Vector()) / 8
                max_dim = max(
                    max(c[i] for c in bbox_corners) - min(c[i] for c in bbox_corners)
                    for i in range(3)
                )

                r3d.view_location = center
                if distance is None:
                    r3d.view_distance = max_dim * 2.0
                else:
                    r3d.view_distance = distance
            elif location:
                r3d.view_location = mathutils.Vector(location)
                if distance is not None:
                    r3d.view_distance = distance
            elif distance is not None:
                r3d.view_distance = distance

            return {
                "success": True,
                "view_location": list(r3d.view_location),
                "view_distance": r3d.view_distance
            }
        except Exception as e:
            return {"error": str(e)}

    def render_views(self, entity_id="render", resolution=800):
        """Render 4 standard views (LEFT, STERN, TOP, BOW) of the scene."""
        import math
        try:
            # Find the mesh object
            obj = None
            for o in bpy.data.objects:
                if o.type == 'MESH':
                    obj = o
                    break
            if not obj:
                return {"error": "No mesh object in scene"}

            scene = bpy.context.scene
            scene.render.engine = 'BLENDER_EEVEE'
            scene.render.resolution_x = resolution
            scene.render.resolution_y = int(resolution * 0.75)

            # Ensure lighting exists
            has_light = any(o.type == 'LIGHT' for o in bpy.data.objects)
            temp_light = None
            if not has_light:
                light_data = bpy.data.lights.new("_render_sun", 'SUN')
                light_data.energy = 5.0
                temp_light = bpy.data.objects.new("_render_sun", light_data)
                temp_light.rotation_euler = (math.radians(45), 0, math.radians(45))
                bpy.context.collection.objects.link(temp_light)

            # Ensure world background
            if not scene.world:
                scene.world = bpy.data.worlds.new("World")
            scene.world.use_nodes = True
            bg = scene.world.node_tree.nodes.get("Background")
            if bg:
                bg.inputs[0].default_value = (0.15, 0.15, 0.15, 1.0)

            # Get bounding box for camera distance
            vs = [obj.matrix_world @ v.co for v in obj.data.vertices]
            max_dim = max(
                max(v[i] for v in vs) - min(v[i] for v in vs) for i in range(3)
            )
            center = mathutils.Vector((
                (max(v.x for v in vs) + min(v.x for v in vs)) / 2,
                (max(v.y for v in vs) + min(v.y for v in vs)) / 2,
                (max(v.z for v in vs) + min(v.z for v in vs)) / 2,
            ))
            dist = max_dim * 1.8

            # Create temp camera
            cam_data = bpy.data.cameras.new("_render_cam")
            cam_data.clip_end = max_dim * 10
            cam_data.lens = 50
            cam_obj = bpy.data.objects.new("_render_cam", cam_data)
            bpy.context.collection.objects.link(cam_obj)
            scene.camera = cam_obj

            # 4 standard views: LEFT (bow on left), STERN (engines face cam), TOP, BOW
            views_config = {
                "LEFT": {"offset": (dist, 0, 0), "rot": (math.radians(90), 0, math.radians(90))},
                "STERN": {"offset": (0, dist, 0), "rot": (math.radians(90), 0, math.radians(180))},
                "TOP": {"offset": (0, 0, dist), "rot": (0, 0, 0)},
                "BOW": {"offset": (dist*0.7, -dist*0.7, dist*0.5), "rot": (math.radians(55), 0, math.radians(45))},
            }

            temp_dir = tempfile.gettempdir()
            rendered = []

            for name, cfg in views_config.items():
                cam_obj.location = (
                    center.x + cfg["offset"][0],
                    center.y + cfg["offset"][1],
                    center.z + cfg["offset"][2]
                )
                cam_obj.rotation_euler = cfg["rot"]

                filepath = os.path.join(temp_dir, f"{entity_id}_{name}.png")
                scene.render.filepath = filepath
                scene.render.image_settings.file_format = 'PNG'
                bpy.ops.render.render(write_still=True)

                rendered.append({
                    "name": name,
                    "path": filepath.replace("\\", "/").replace("C:/Users", "/mnt/c/Users")
                })

            # Cleanup temp objects
            bpy.data.objects.remove(cam_obj)
            bpy.data.cameras.remove(cam_data)
            if temp_light:
                light_data_ref = temp_light.data
                bpy.data.objects.remove(temp_light)
                bpy.data.lights.remove(light_data_ref)

            return {"success": True, "views": rendered}
        except Exception as e:
            return {"error": str(e)}

    def get_mesh_stats(self, name=None):
        """Get mesh statistics for an object."""
        try:
            obj = None
            if name:
                obj = bpy.data.objects.get(name)
            else:
                # Find first mesh object
                for o in bpy.data.objects:
                    if o.type == 'MESH':
                        obj = o
                        break

            if not obj or obj.type != 'MESH':
                return {"error": f"No mesh object found{' named ' + name if name else ''}"}

            mesh = obj.data
            vs = [obj.matrix_world @ v.co for v in mesh.vertices]

            spans = {}
            for ax, i in [('X', 0), ('Y', 1), ('Z', 2)]:
                vals = [v[i] for v in vs]
                spans[ax] = round(max(vals) - min(vals), 1)

            # Collect meshpoint info (child empties)
            meshpoints = []
            for child in obj.children:
                if child.type == 'EMPTY':
                    meshpoints.append({
                        "name": child.name,
                        "location": [round(child.location.x, 2), round(child.location.y, 2), round(child.location.z, 2)]
                    })

            materials = [mat.name for mat in mesh.materials if mat]

            return {
                "name": obj.name,
                "vertices": len(mesh.vertices),
                "faces": len(mesh.polygons),
                "triangles": sum(1 for p in mesh.polygons if len(p.vertices) == 3),
                "materials": materials,
                "meshpoints": meshpoints,
                "meshpoint_count": len(meshpoints),
                "spans": spans,
                "location": [round(obj.location.x, 2), round(obj.location.y, 2), round(obj.location.z, 2)],
                "scale": [round(obj.scale.x, 3), round(obj.scale.y, 3), round(obj.scale.z, 3)]
            }
        except Exception as e:
            return {"error": str(e)}

    def import_sins2_mesh(self, mesh_path, add_meshpoints=True):
        """Import a SoSE2 .mesh file via BinaryReader."""
        import math, sys
        try:
            # Clear scene
            bpy.ops.object.select_all(action='SELECT')
            bpy.ops.object.delete()

            # Import via BinaryReader
            ext_path = r"C:\Users\gabes\AppData\Roaming\Blender Foundation\Blender\5.0\scripts\addons\sins2_blender_extension"
            if ext_path not in sys.path:
                sys.path.insert(0, ext_path)
            from src.lib.binary_reader import BinaryReader

            md = BinaryReader.initialize_from(mesh_file=mesh_path).mesh_data

            # Build mesh with game→blender coordinate conversion
            verts = [(v['p'][0], -v['p'][2], v['p'][1]) for v in md['vertices']]
            faces = [(md['indices'][i], md['indices'][i+1], md['indices'][i+2])
                     for i in range(0, len(md['indices']), 3)]

            # Extract entity name from path
            entity_id = os.path.splitext(os.path.basename(mesh_path))[0]

            mesh = bpy.data.meshes.new(entity_id)
            mesh.from_pydata(verts, [], faces)
            mesh.update()

            obj = bpy.data.objects.new(entity_id, mesh)
            bpy.context.collection.objects.link(obj)

            # Center origin
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
            bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')
            obj.location = (0, 0, 0)

            # Add meshpoints as empties
            mp_count = 0
            if add_meshpoints and md.get('meshpoints'):
                for mp in md['meshpoints']:
                    gx, gy, gz = mp['position']
                    empty = bpy.data.objects.new(mp['name'], None)
                    empty.location = mathutils.Vector((gx, -gz, gy))  # game→blender
                    empty.empty_display_type = 'ARROWS'
                    empty.empty_display_size = 5.0
                    if 'exhaust' in mp['name']:
                        empty.rotation_euler = (math.radians(90), 0, 0)
                    bpy.context.collection.objects.link(empty)
                    empty.parent = obj
                    mp_count += 1

            # Add sun light
            light_data = bpy.data.lights.new("Sun", 'SUN')
            light_data.energy = 5.0
            light_obj = bpy.data.objects.new("Sun", light_data)
            light_obj.rotation_euler = (math.radians(45), 0, math.radians(45))
            bpy.context.collection.objects.link(light_obj)

            # Calculate spans
            vs = [v.co for v in obj.data.vertices]
            spans = {}
            for ax, i in [('X', 0), ('Y', 1), ('Z', 2)]:
                vals = [v[i] for v in vs]
                spans[ax] = round(max(vals) - min(vals), 1)

            return {
                "success": True,
                "entity_id": entity_id,
                "vertices": len(md['vertices']),
                "faces": len(md['indices']) // 3,
                "meshpoints": mp_count,
                "materials": md.get('materials', []),
                "spans": spans
            }
        except Exception as e:
            return {"error": str(e)}

    #region Sketchfab API
    def get_sketchfab_status(self):
        """Get the current status of Sketchfab integration"""
        enabled = bpy.context.scene.blendermcp_use_sketchfab
        api_key = bpy.context.scene.blendermcp_sketchfab_api_key

        # Test the API key if present
        if api_key:
            try:
                headers = {
                    "Authorization": f"Token {api_key}"
                }

                response = requests.get(
                    "https://api.sketchfab.com/v3/me",
                    headers=headers,
                    timeout=30
                )

                if response.status_code == 200:
                    user_data = response.json()
                    username = user_data.get("username", "Unknown user")
                    return {
                        "enabled": True,
                        "message": f"Sketchfab integration is enabled and ready to use. Logged in as: {username}"
                    }
                else:
                    return {
                        "enabled": False,
                        "message": f"Sketchfab API key seems invalid. Status code: {response.status_code}"
                    }
            except requests.exceptions.Timeout:
                return {
                    "enabled": False,
                    "message": "Timeout connecting to Sketchfab API. Check your internet connection."
                }
            except Exception as e:
                return {
                    "enabled": False,
                    "message": f"Error testing Sketchfab API key: {str(e)}"
                }

        if enabled and api_key:
            return {"enabled": True, "message": "Sketchfab integration is enabled and ready to use."}
        elif enabled and not api_key:
            return {
                "enabled": False,
                "message": """Sketchfab integration is currently enabled, but API key is not given. To enable it:
                            1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                            2. Keep the 'Use Sketchfab' checkbox checked
                            3. Enter your Sketchfab API Key
                            4. Restart the connection to Claude"""
            }
        else:
            return {
                "enabled": False,
                "message": """Sketchfab integration is currently disabled. To enable it:
                            1. In the 3D Viewport, find the BlenderMCP panel in the sidebar (press N if hidden)
                            2. Check the 'Use assets from Sketchfab' checkbox
                            3. Enter your Sketchfab API Key
                            4. Restart the connection to Claude"""
            }

    def search_sketchfab_models(self, query, categories=None, count=20, downloadable=True):
        """Search for models on Sketchfab based on query and optional filters"""
        try:
            api_key = bpy.context.scene.blendermcp_sketchfab_api_key
            if not api_key:
                return {"error": "Sketchfab API key is not configured"}

            params = {
                "type": "models",
                "q": query,
                "count": count,
                "downloadable": downloadable,
                "archives_flavours": False
            }

            if categories:
                params["categories"] = categories

            headers = {
                "Authorization": f"Token {api_key}"
            }

            response = requests.get(
                "https://api.sketchfab.com/v3/search",
                headers=headers,
                params=params,
                timeout=30
            )

            if response.status_code == 401:
                return {"error": "Authentication failed (401). Check your API key."}

            if response.status_code != 200:
                return {"error": f"API request failed with status code {response.status_code}"}

            response_data = response.json()

            if response_data is None:
                return {"error": "Received empty response from Sketchfab API"}

            results = response_data.get("results", [])
            if not isinstance(results, list):
                return {"error": f"Unexpected response format from Sketchfab API: {response_data}"}

            return response_data

        except requests.exceptions.Timeout:
            return {"error": "Request timed out. Check your internet connection."}
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON response from Sketchfab API: {str(e)}"}
        except Exception as e:
            traceback.print_exc()
            return {"error": str(e)}

    def get_sketchfab_model_preview(self, uid):
        """Get thumbnail preview image of a Sketchfab model by its UID"""
        try:
            import base64

            api_key = bpy.context.scene.blendermcp_sketchfab_api_key
            if not api_key:
                return {"error": "Sketchfab API key is not configured"}

            headers = {"Authorization": f"Token {api_key}"}

            response = requests.get(
                f"https://api.sketchfab.com/v3/models/{uid}",
                headers=headers,
                timeout=30
            )

            if response.status_code == 401:
                return {"error": "Authentication failed (401). Check your API key."}

            if response.status_code == 404:
                return {"error": f"Model not found: {uid}"}

            if response.status_code != 200:
                return {"error": f"Failed to get model info: {response.status_code}"}

            data = response.json()
            thumbnails = data.get("thumbnails", {}).get("images", [])

            if not thumbnails:
                return {"error": "No thumbnail available for this model"}

            # Find a suitable thumbnail (prefer medium size ~640px)
            selected_thumbnail = None
            for thumb in thumbnails:
                width = thumb.get("width", 0)
                if 400 <= width <= 800:
                    selected_thumbnail = thumb
                    break

            # Fallback to the first available thumbnail
            if not selected_thumbnail:
                selected_thumbnail = thumbnails[0]

            thumbnail_url = selected_thumbnail.get("url")
            if not thumbnail_url:
                return {"error": "Thumbnail URL not found"}

            # Download the thumbnail image
            img_response = requests.get(thumbnail_url, timeout=30)
            if img_response.status_code != 200:
                return {"error": f"Failed to download thumbnail: {img_response.status_code}"}

            # Encode image as base64
            image_data = base64.b64encode(img_response.content).decode('ascii')

            # Determine format from content type or URL
            content_type = img_response.headers.get("Content-Type", "")
            if "png" in content_type or thumbnail_url.endswith(".png"):
                img_format = "png"
            else:
                img_format = "jpeg"

            model_name = data.get("name", "Unknown")
            author = data.get("user", {}).get("username", "Unknown")

            return {
                "success": True,
                "image_data": image_data,
                "format": img_format,
                "model_name": model_name,
                "author": author,
                "uid": uid,
                "thumbnail_width": selected_thumbnail.get("width"),
                "thumbnail_height": selected_thumbnail.get("height")
            }

        except requests.exceptions.Timeout:
            return {"error": "Request timed out. Check your internet connection."}
        except Exception as e:
            traceback.print_exc()
            return {"error": f"Failed to get model preview: {str(e)}"}

    def download_sketchfab_model(self, uid, normalize_size=False, target_size=1.0):
        """Download a model from Sketchfab by its UID

        Parameters:
        - uid: The unique identifier of the Sketchfab model
        - normalize_size: If True, scale the model so its largest dimension equals target_size
        - target_size: The target size in Blender units (meters) for the largest dimension
        """
        try:
            api_key = bpy.context.scene.blendermcp_sketchfab_api_key
            if not api_key:
                return {"error": "Sketchfab API key is not configured"}

            headers = {
                "Authorization": f"Token {api_key}"
            }

            download_endpoint = f"https://api.sketchfab.com/v3/models/{uid}/download"

            response = requests.get(
                download_endpoint,
                headers=headers,
                timeout=30
            )

            if response.status_code == 401:
                return {"error": "Authentication failed (401). Check your API key."}

            if response.status_code != 200:
                return {"error": f"Download request failed with status code {response.status_code}"}

            data = response.json()

            if data is None:
                return {"error": "Received empty response from Sketchfab API for download request"}

            gltf_data = data.get("gltf")
            if not gltf_data:
                return {"error": "No gltf download URL available for this model. Response: " + str(data)}

            download_url = gltf_data.get("url")
            if not download_url:
                return {"error": "No download URL available for this model. Make sure the model is downloadable and you have access."}

            model_response = requests.get(download_url, timeout=60)

            if model_response.status_code != 200:
                return {"error": f"Model download failed with status code {model_response.status_code}"}

            # Save to temporary file
            temp_dir = tempfile.mkdtemp()
            zip_file_path = os.path.join(temp_dir, f"{uid}.zip")

            with open(zip_file_path, "wb") as f:
                f.write(model_response.content)

            # Extract the zip file with enhanced security
            with zipfile.ZipFile(zip_file_path, 'r') as zip_ref:
                for file_info in zip_ref.infolist():
                    file_path = file_info.filename
                    target_path = os.path.join(temp_dir, os.path.normpath(file_path))
                    abs_temp_dir = os.path.abspath(temp_dir)
                    abs_target_path = os.path.abspath(target_path)

                    if not abs_target_path.startswith(abs_temp_dir):
                        with suppress(Exception):
                            shutil.rmtree(temp_dir)
                        return {"error": "Security issue: Zip contains files with path traversal attempt"}

                    if ".." in file_path:
                        with suppress(Exception):
                            shutil.rmtree(temp_dir)
                        return {"error": "Security issue: Zip contains files with directory traversal sequence"}

                zip_ref.extractall(temp_dir)

            # Find the main glTF file
            gltf_files = [f for f in os.listdir(temp_dir) if f.endswith('.gltf') or f.endswith('.glb')]

            if not gltf_files:
                with suppress(Exception):
                    shutil.rmtree(temp_dir)
                return {"error": "No glTF file found in the downloaded model"}

            main_file = os.path.join(temp_dir, gltf_files[0])

            # Import the model
            bpy.ops.import_scene.gltf(filepath=main_file)

            # Get the imported objects
            imported_objects = list(bpy.context.selected_objects)
            imported_object_names = [obj.name for obj in imported_objects]

            # Clean up temporary files
            with suppress(Exception):
                shutil.rmtree(temp_dir)

            # Find root objects (objects without parents in the imported set)
            root_objects = [obj for obj in imported_objects if obj.parent is None]

            # Helper function to recursively get all mesh children
            def get_all_mesh_children(obj):
                """Recursively collect all mesh objects in the hierarchy"""
                meshes = []
                if obj.type == 'MESH':
                    meshes.append(obj)
                for child in obj.children:
                    meshes.extend(get_all_mesh_children(child))
                return meshes

            # Collect ALL meshes from the entire hierarchy (starting from roots)
            all_meshes = []
            for obj in root_objects:
                all_meshes.extend(get_all_mesh_children(obj))

            if all_meshes:
                # Calculate combined world bounding box for all meshes
                all_min = mathutils.Vector((float('inf'), float('inf'), float('inf')))
                all_max = mathutils.Vector((float('-inf'), float('-inf'), float('-inf')))

                for mesh_obj in all_meshes:
                    for corner in mesh_obj.bound_box:
                        world_corner = mesh_obj.matrix_world @ mathutils.Vector(corner)
                        all_min.x = min(all_min.x, world_corner.x)
                        all_min.y = min(all_min.y, world_corner.y)
                        all_min.z = min(all_min.z, world_corner.z)
                        all_max.x = max(all_max.x, world_corner.x)
                        all_max.y = max(all_max.y, world_corner.y)
                        all_max.z = max(all_max.z, world_corner.z)

                dimensions = [
                    all_max.x - all_min.x,
                    all_max.y - all_min.y,
                    all_max.z - all_min.z
                ]
                max_dimension = max(dimensions)

                # Apply normalization if requested
                scale_applied = 1.0
                if normalize_size and max_dimension > 0:
                    scale_factor = target_size / max_dimension
                    scale_applied = scale_factor

                    for root in root_objects:
                        root.scale = (
                            root.scale.x * scale_factor,
                            root.scale.y * scale_factor,
                            root.scale.z * scale_factor
                        )

                    bpy.context.view_layer.update()

                    all_min = mathutils.Vector((float('inf'), float('inf'), float('inf')))
                    all_max = mathutils.Vector((float('-inf'), float('-inf'), float('-inf')))

                    for mesh_obj in all_meshes:
                        for corner in mesh_obj.bound_box:
                            world_corner = mesh_obj.matrix_world @ mathutils.Vector(corner)
                            all_min.x = min(all_min.x, world_corner.x)
                            all_min.y = min(all_min.y, world_corner.y)
                            all_min.z = min(all_min.z, world_corner.z)
                            all_max.x = max(all_max.x, world_corner.x)
                            all_max.y = max(all_max.y, world_corner.y)
                            all_max.z = max(all_max.z, world_corner.z)

                    dimensions = [
                        all_max.x - all_min.x,
                        all_max.y - all_min.y,
                        all_max.z - all_min.z
                    ]

                world_bounding_box = [[all_min.x, all_min.y, all_min.z], [all_max.x, all_max.y, all_max.z]]
            else:
                world_bounding_box = None
                dimensions = None
                scale_applied = 1.0

            result = {
                "success": True,
                "message": "Model imported successfully",
                "imported_objects": imported_object_names
            }

            if world_bounding_box:
                result["world_bounding_box"] = world_bounding_box
            if dimensions:
                result["dimensions"] = [round(d, 4) for d in dimensions]
            if normalize_size:
                result["scale_applied"] = round(scale_applied, 6)
                result["normalized"] = True

            return result

        except requests.exceptions.Timeout:
            return {"error": "Request timed out. Check your internet connection and try again with a simpler model."}
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON response from Sketchfab API: {str(e)}"}
        except Exception as e:
            traceback.print_exc()
            return {"error": f"Failed to download model: {str(e)}"}

    @staticmethod
    def _clean_imported_glb(filepath, mesh_name=None):
        # Get the set of existing objects before import
        existing_objects = set(bpy.data.objects)

        # Import the GLB file
        bpy.ops.import_scene.gltf(filepath=filepath)

        # Ensure the context is updated
        bpy.context.view_layer.update()

        # Get all imported objects
        imported_objects = list(set(bpy.data.objects) - existing_objects)

        if not imported_objects:
            print("Error: No objects were imported.")
            return

        # Identify the mesh object
        mesh_obj = None

        if len(imported_objects) == 1 and imported_objects[0].type == 'MESH':
            mesh_obj = imported_objects[0]
            print("Single mesh imported, no cleanup needed.")
        else:
            if len(imported_objects) == 2:
                empty_objs = [i for i in imported_objects if i.type == "EMPTY"]
                if len(empty_objs) != 1:
                    print("Error: Expected an empty node with one mesh child or a single mesh object.")
                    return
                parent_obj = empty_objs.pop()
                if len(parent_obj.children) == 1:
                    potential_mesh = parent_obj.children[0]
                    if potential_mesh.type == 'MESH':
                        print("GLB structure confirmed: Empty node with one mesh child.")

                        # Unparent the mesh from the empty node
                        potential_mesh.parent = None

                        # Remove the empty node
                        bpy.data.objects.remove(parent_obj)
                        print("Removed empty node, keeping only the mesh.")

                        mesh_obj = potential_mesh
                    else:
                        print("Error: Child is not a mesh object.")
                        return
                else:
                    print("Error: Expected an empty node with one mesh child or a single mesh object.")
                    return
            else:
                print("Error: Expected an empty node with one mesh child or a single mesh object.")
                return

        # Rename the mesh if needed
        try:
            if mesh_obj and mesh_obj.name is not None and mesh_name:
                mesh_obj.name = mesh_name
                if mesh_obj.data.name is not None:
                    mesh_obj.data.name = mesh_name
                print(f"Mesh renamed to: {mesh_name}")
        except Exception as e:
            print("Having issue with renaming, give up renaming.")

        return mesh_obj
    #endregion


# Blender UI Panel
class BLENDERMCP_PT_Panel(bpy.types.Panel):
    bl_label = "Blender MCP"
    bl_idname = "BLENDERMCP_PT_Panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BlenderMCP'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.prop(scene, "blendermcp_port")

        layout.prop(scene, "blendermcp_use_sketchfab", text="Use assets from Sketchfab")
        if scene.blendermcp_use_sketchfab:
            layout.prop(scene, "blendermcp_sketchfab_api_key", text="API Key")

        if not scene.blendermcp_server_running:
            layout.operator("blendermcp.start_server", text="Connect to MCP server")
        else:
            layout.operator("blendermcp.stop_server", text="Disconnect from MCP server")
            layout.label(text=f"Running on port {scene.blendermcp_port}")


# Operator to start the server
class BLENDERMCP_OT_StartServer(bpy.types.Operator):
    bl_idname = "blendermcp.start_server"
    bl_label = "Connect to Claude"
    bl_description = "Start the BlenderMCP server to connect with Claude"

    def execute(self, context):
        scene = context.scene

        # Create a new server instance
        if not hasattr(bpy.types, "blendermcp_server") or not bpy.types.blendermcp_server:
            bpy.types.blendermcp_server = BlenderMCPServer(port=scene.blendermcp_port)

        # Start the server
        bpy.types.blendermcp_server.start()
        scene.blendermcp_server_running = True

        return {'FINISHED'}


# Operator to stop the server
class BLENDERMCP_OT_StopServer(bpy.types.Operator):
    bl_idname = "blendermcp.stop_server"
    bl_label = "Stop the connection to Claude"
    bl_description = "Stop the connection to Claude"

    def execute(self, context):
        scene = context.scene

        # Stop the server if it exists
        if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
            bpy.types.blendermcp_server.stop()
            del bpy.types.blendermcp_server

        scene.blendermcp_server_running = False

        return {'FINISHED'}


# Registration functions
def register():
    bpy.types.Scene.blendermcp_port = IntProperty(
        name="Port",
        description="Port for the BlenderMCP server",
        default=9876,
        min=1024,
        max=65535
    )

    bpy.types.Scene.blendermcp_server_running = BoolProperty(
        name="Server Running",
        default=False
    )

    bpy.types.Scene.blendermcp_use_sketchfab = BoolProperty(
        name="Use Sketchfab",
        description="Enable Sketchfab asset integration",
        default=False
    )

    bpy.types.Scene.blendermcp_sketchfab_api_key = StringProperty(
        name="Sketchfab API Key",
        subtype="PASSWORD",
        description="API Key provided by Sketchfab",
        default=""
    )

    bpy.utils.register_class(BLENDERMCP_PT_Panel)
    bpy.utils.register_class(BLENDERMCP_OT_StartServer)
    bpy.utils.register_class(BLENDERMCP_OT_StopServer)

    print("BlenderMCP addon registered")


def unregister():
    # Stop the server if it's running
    if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
        bpy.types.blendermcp_server.stop()
        del bpy.types.blendermcp_server

    bpy.utils.unregister_class(BLENDERMCP_PT_Panel)
    bpy.utils.unregister_class(BLENDERMCP_OT_StartServer)
    bpy.utils.unregister_class(BLENDERMCP_OT_StopServer)

    del bpy.types.Scene.blendermcp_port
    del bpy.types.Scene.blendermcp_server_running
    del bpy.types.Scene.blendermcp_use_sketchfab
    del bpy.types.Scene.blendermcp_sketchfab_api_key

    print("BlenderMCP addon unregistered")


if __name__ == "__main__":
    register()
