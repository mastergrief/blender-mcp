# blender_mcp_server.py
from mcp.server.fastmcp import FastMCP, Context, Image
import socket
import json
import asyncio
import logging
import tempfile
from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any, List
import os
from pathlib import Path
import base64

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("BlenderMCPServer")

# Default configuration
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9876

@dataclass
class BlenderConnection:
    host: str
    port: int
    sock: socket.socket = None  # Changed from 'socket' to 'sock' to avoid naming conflict

    def connect(self) -> bool:
        """Connect to the Blender addon socket server"""
        if self.sock:
            return True

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            logger.info(f"Connected to Blender at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Blender: {str(e)}")
            self.sock = None
            return False

    def disconnect(self):
        """Disconnect from the Blender addon"""
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Blender: {str(e)}")
            finally:
                self.sock = None

    def receive_full_response(self, sock, buffer_size=8192):
        """Receive the complete response, potentially in multiple chunks"""
        chunks = []
        # Use a consistent timeout value that matches the addon's timeout
        sock.settimeout(180.0)  # Match the addon's timeout

        try:
            while True:
                try:
                    chunk = sock.recv(buffer_size)
                    if not chunk:
                        # If we get an empty chunk, the connection might be closed
                        if not chunks:  # If we haven't received anything yet, this is an error
                            raise Exception("Connection closed before receiving any data")
                        break

                    chunks.append(chunk)

                    # Check if we've received a complete JSON object
                    try:
                        data = b''.join(chunks)
                        json.loads(data.decode('utf-8'))
                        # If we get here, it parsed successfully
                        logger.info(f"Received complete response ({len(data)} bytes)")
                        return data
                    except json.JSONDecodeError:
                        # Incomplete JSON, continue receiving
                        continue
                except socket.timeout:
                    # If we hit a timeout during receiving, break the loop and try to use what we have
                    logger.warning("Socket timeout during chunked receive")
                    break
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error during receive: {str(e)}")
                    raise  # Re-raise to be handled by the caller
        except socket.timeout:
            logger.warning("Socket timeout during chunked receive")
        except Exception as e:
            logger.error(f"Error during receive: {str(e)}")
            raise

        # If we get here, we either timed out or broke out of the loop
        # Try to use what we have
        if chunks:
            data = b''.join(chunks)
            logger.info(f"Returning data after receive completion ({len(data)} bytes)")
            try:
                # Try to parse what we have
                json.loads(data.decode('utf-8'))
                return data
            except json.JSONDecodeError:
                # If we can't parse it, it's incomplete
                raise Exception("Incomplete JSON response received")
        else:
            raise Exception("No data received")

    def send_command(self, command_type: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Send a command to Blender and return the response"""
        if not self.sock and not self.connect():
            raise ConnectionError("Not connected to Blender")

        command = {
            "type": command_type,
            "params": params or {}
        }

        try:
            # Log the command being sent
            logger.info(f"Sending command: {command_type} with params: {params}")

            # Send the command
            self.sock.sendall(json.dumps(command).encode('utf-8'))
            logger.info(f"Command sent, waiting for response...")

            # Set a timeout for receiving - use the same timeout as in receive_full_response
            self.sock.settimeout(180.0)  # Match the addon's timeout

            # Receive the response using the improved receive_full_response method
            response_data = self.receive_full_response(self.sock)
            logger.info(f"Received {len(response_data)} bytes of data")

            response = json.loads(response_data.decode('utf-8'))
            logger.info(f"Response parsed, status: {response.get('status', 'unknown')}")

            if response.get("status") == "error":
                logger.error(f"Blender error: {response.get('message')}")
                raise Exception(response.get("message", "Unknown error from Blender"))

            return response.get("result", {})
        except socket.timeout:
            logger.error("Socket timeout while waiting for response from Blender")
            # Don't try to reconnect here - let the get_blender_connection handle reconnection
            # Just invalidate the current socket so it will be recreated next time
            self.sock = None
            raise Exception("Timeout waiting for Blender response - try simplifying your request")
        except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
            logger.error(f"Socket connection error: {str(e)}")
            self.sock = None
            raise Exception(f"Connection to Blender lost: {str(e)}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from Blender: {str(e)}")
            # Try to log what was received
            if 'response_data' in locals() and response_data:
                logger.error(f"Raw response (first 200 bytes): {response_data[:200]}")
            raise Exception(f"Invalid response from Blender: {str(e)}")
        except Exception as e:
            logger.error(f"Error communicating with Blender: {str(e)}")
            # Don't try to reconnect here - let the get_blender_connection handle reconnection
            self.sock = None
            raise Exception(f"Communication error with Blender: {str(e)}")

@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """Manage server startup and shutdown lifecycle"""
    # We don't need to create a connection here since we're using the global connection
    # for resources and tools

    try:
        # Just log that we're starting up
        logger.info("BlenderMCP server starting up")

        # Try to connect to Blender on startup to verify it's available
        try:
            # This will initialize the global connection if needed
            blender = get_blender_connection()
            logger.info("Successfully connected to Blender on startup")
        except Exception as e:
            logger.warning(f"Could not connect to Blender on startup: {str(e)}")
            logger.warning("Make sure the Blender addon is running before using Blender resources or tools")

        # Return an empty context - we're using the global connection
        yield {}
    finally:
        # Clean up the global connection on shutdown
        global _blender_connection
        if _blender_connection:
            logger.info("Disconnecting from Blender on shutdown")
            _blender_connection.disconnect()
            _blender_connection = None
        logger.info("BlenderMCP server shut down")

# Create the MCP server with lifespan support
mcp = FastMCP(
    "BlenderMCP",
    lifespan=server_lifespan
)

# Resource endpoints

# Global connection for resources (since resources can't access context)
_blender_connection = None

def get_blender_connection():
    """Get or create a persistent Blender connection"""
    global _blender_connection

    # If we have an existing connection, check if it's still valid
    if _blender_connection is not None:
        try:
            # Send a ping to verify the connection is alive
            _blender_connection.send_command("get_scene_info")
            return _blender_connection
        except Exception as e:
            # Connection is dead, close it and create a new one
            logger.warning(f"Existing connection is no longer valid: {str(e)}")
            try:
                _blender_connection.disconnect()
            except:
                pass
            _blender_connection = None

    # Create a new connection if needed
    if _blender_connection is None:
        host = os.getenv("BLENDER_HOST", DEFAULT_HOST)
        port = int(os.getenv("BLENDER_PORT", DEFAULT_PORT))
        _blender_connection = BlenderConnection(host=host, port=port)
        if not _blender_connection.connect():
            logger.error("Failed to connect to Blender")
            _blender_connection = None
            raise Exception("Could not connect to Blender. Make sure the Blender addon is running.")
        logger.info("Created new persistent connection to Blender")

    return _blender_connection


@mcp.tool()
def get_scene_info(ctx: Context) -> str:
    """Get detailed information about the current Blender scene"""
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_scene_info")

        # Just return the JSON representation of what Blender sent us
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting scene info from Blender: {str(e)}")
        return f"Error getting scene info: {str(e)}"

@mcp.tool()
def get_object_info(ctx: Context, object_name: str) -> str:
    """
    Get detailed information about a specific object in the Blender scene.

    Parameters:
    - object_name: The name of the object to get information about
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_object_info", {"name": object_name})

        # Just return the JSON representation of what Blender sent us
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting object info from Blender: {str(e)}")
        return f"Error getting object info: {str(e)}"

@mcp.tool()
def get_viewport_screenshot(ctx: Context, max_size: int = 800) -> Image:
    """
    Capture a screenshot of the current Blender 3D viewport.

    Parameters:
    - max_size: Maximum size in pixels for the largest dimension (default: 800)

    Returns the screenshot as an Image.
    """
    try:
        blender = get_blender_connection()

        result = blender.send_command("get_viewport_screenshot", {
            "max_size": max_size,
            "format": "png"
        })

        if "error" in result:
            raise Exception(result["error"])

        # Addon returns base64-encoded image data (avoids cross-OS path issues)
        if "image_b64" in result:
            import base64
            image_bytes = base64.b64decode(result["image_b64"])
            return Image(data=image_bytes, format="png")

        raise Exception("No image data in response from Blender addon")

    except Exception as e:
        logger.error(f"Error capturing screenshot: {str(e)}")
        raise Exception(f"Screenshot failed: {str(e)}")


@mcp.tool()
def execute_blender_code(ctx: Context, code: str) -> str:
    """
    Execute arbitrary Python code in Blender. Make sure to do it step-by-step by breaking it into smaller chunks.

    Parameters:
    - code: The Python code to execute
    """
    try:
        # Get the global connection
        blender = get_blender_connection()
        result = blender.send_command("execute_code", {"code": code})
        return f"Code executed successfully: {result.get('result', '')}"
    except Exception as e:
        logger.error(f"Error executing code: {str(e)}")
        return f"Error executing code: {str(e)}"

@mcp.tool()
def navigate_viewport(ctx: Context, target: str = None, location: list = None, distance: float = None, view: str = None) -> str:
    """
    Navigate the 3D viewport. Frame an object, set position, or use preset views.

    Parameters:
    - target: Object name to frame/focus on
    - location: Optional [x, y, z] point to look at
    - distance: Optional viewing distance
    - view: Preset view: "front", "side", "top", "persp" (sets camera angle)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("navigate_viewport", {
            "target": target,
            "location": location,
            "distance": distance,
            "view": view,
        })
        if "error" in result:
            raise Exception(result["error"])
        return json.dumps(result)
    except Exception as e:
        logger.error(f"Error navigating viewport: {str(e)}")
        return f"Error navigating viewport: {str(e)}"


@mcp.tool()
def render_views(ctx: Context, entity_id: str, resolution: int = 800, object_name: str = None) -> str:
    """
    Render 4 standard views (LEFT, STERN, TOP, BOW) with 3-point lighting.
    Returns images inline as base64 — no need to Read files separately.

    Parameters:
    - entity_id: Name prefix for the render files
    - resolution: Width in pixels (default 800)
    - object_name: Specific object to render (default: first mesh)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("render_views", {
            "entity_id": entity_id,
            "resolution": resolution,
            "object_name": object_name
        })
        if "error" in result:
            raise Exception(result["error"])

        views = result.get("views", [])
        output_parts = []
        for v in views:
            if "image_b64" in v:
                output_parts.append(f"[{v['name']}]")
            else:
                output_parts.append(f"{v['name']}: {v.get('path', 'no path')}")

        return f"Rendered {len(views)} views: {', '.join(output_parts)}\nRead the PNG files at /mnt/c/Users/gabes/AppData/Local/Temp/{entity_id}_*.png to verify."
    except Exception as e:
        logger.error(f"Error rendering views: {str(e)}")
        return f"Error rendering views: {str(e)}"


@mcp.tool()
def get_mesh_stats(ctx: Context, object_name: str = None) -> str:
    """
    Get mesh statistics: vertex/face count, meshpoints, materials, bounding box spans.

    Parameters:
    - object_name: Name of mesh object (default: active object or first mesh)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_mesh_stats", {"name": object_name})
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting mesh stats: {str(e)}")
        return f"Error getting mesh stats: {str(e)}"


@mcp.tool()
def import_sins2_mesh(ctx: Context, mesh_path: str = None, entity_id: str = None, add_meshpoints: bool = True, normalize: bool = False) -> str:
    """
    Import a SoSE2 .mesh file with game-to-Blender coordinate conversion.

    Parameters:
    - mesh_path: Full Windows path to .mesh file (use this OR entity_id)
    - entity_id: Entity name shorthand (e.g. "trader_orbital_cannon") — auto-resolves to mod or game path
    - add_meshpoints: Add meshpoints as empties (default True)
    - normalize: Scale to 100 units longest dimension (default False)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("import_sins2_mesh", {
            "mesh_path": mesh_path,
            "entity_id": entity_id,
            "add_meshpoints": add_meshpoints,
            "normalize": normalize
        })
        if "error" in result:
            raise Exception(result["error"])

        output = f"Imported: {result.get('entity_id', '?')}\n"
        output += f"  Vertices:   {result.get('vertices', '?'):,}\n"
        output += f"  Faces:      {result.get('faces', '?'):,}\n"
        output += f"  Meshpoints: {result.get('meshpoints', '?')}\n"
        output += f"  Materials:  {result.get('materials', [])}\n"
        spans = result.get('spans', {})
        if spans:
            output += f"  Spans: X={spans.get('X', '?')}, Y={spans.get('Y', '?')}, Z={spans.get('Z', '?')}\n"
        if result.get('normalized'):
            output += f"  Normalized to {result.get('normalized_size', 100)} units\n"
        return output
    except Exception as e:
        logger.error(f"Error importing sins2 mesh: {str(e)}")
        return f"Error importing sins2 mesh: {str(e)}"


@mcp.tool()
def export_sins2_mesh(ctx: Context, entity_id: str, copy_to_repo: bool = True) -> str:
    """
    Export current mesh as SoSE2 .mesh with full post-processing pipeline.
    Handles: triangulation, UVs, material naming, export, exhaust rotation fix,
    meshpoint suffix fix, and optional copy to mod repo.

    Parameters:
    - entity_id: Entity name (e.g. "trader_orbital_cannon") — used for filename and material
    - copy_to_repo: Copy exported mesh to mods/halo-total-conversion/meshes/ (default True)
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("export_sins2_mesh", {
            "entity_id": entity_id,
            "copy_to_repo": copy_to_repo
        })
        if "error" in result:
            raise Exception(result["error"])

        output = f"Exported: {entity_id}.mesh\n"
        output += f"  Vertices: {result.get('vertices', '?'):,}\n"
        output += f"  Meshpoints: {result.get('meshpoints', '?')}\n"
        output += f"  Material: {result.get('material', '?')}\n"
        fixes = result.get('fixes', {})
        if fixes:
            output += f"  Exhaust rotations fixed: {fixes.get('exhaust_rotations', 0)}\n"
            output += f"  Suffix cleanups: {fixes.get('suffix_cleanups', 0)}\n"
        if result.get('repo_path'):
            output += f"  Copied to: {result['repo_path']}\n"
        return output
    except Exception as e:
        logger.error(f"Error exporting sins2 mesh: {str(e)}")
        return f"Error exporting sins2 mesh: {str(e)}"


@mcp.tool()
def add_base_meshpoints(ctx: Context, entity_id: str) -> str:
    """
    Read meshpoints from a base game mesh and add them to the current Blender model.
    The meshpoints are added as empties parented to the first mesh object.

    Parameters:
    - entity_id: Base game entity name (e.g. "trader_light_frigate")
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("add_base_meshpoints", {
            "entity_id": entity_id
        })
        if "error" in result:
            raise Exception(result["error"])

        output = f"Added {result.get('count', 0)} meshpoints from {entity_id}\n"
        names = result.get('meshpoint_names', [])
        unique = sorted(set(n.split('.')[0] for n in names))
        output += f"  Types: {', '.join(unique)}\n"
        return output
    except Exception as e:
        logger.error(f"Error adding meshpoints: {str(e)}")
        return f"Error adding meshpoints: {str(e)}"


@mcp.tool()
def get_sketchfab_status(ctx: Context) -> str:
    """
    Check if Sketchfab integration is enabled in Blender.
    Returns a message indicating whether Sketchfab features are available.
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_sketchfab_status")
        enabled = result.get("enabled", False)
        message = result.get("message", "")
        if enabled:
            message += "Sketchfab is good at Realistic models, and has a wider variety of models than PolyHaven."
        return message
    except Exception as e:
        logger.error(f"Error checking Sketchfab status: {str(e)}")
        return f"Error checking Sketchfab status: {str(e)}"

@mcp.tool()
def search_sketchfab_models(
    ctx: Context,
    query: str,
    categories: str = None,
    count: int = 20,
    downloadable: bool = True
) -> str:
    """
    Search for models on Sketchfab with optional filtering.

    Parameters:
    - query: Text to search for
    - categories: Optional comma-separated list of categories
    - count: Maximum number of results to return (default 20)
    - downloadable: Whether to include only downloadable models (default True)

    Returns a formatted list of matching models.
    """
    try:
        blender = get_blender_connection()
        logger.info(f"Searching Sketchfab models with query: {query}, categories: {categories}, count: {count}, downloadable: {downloadable}")
        result = blender.send_command("search_sketchfab_models", {
            "query": query,
            "categories": categories,
            "count": count,
            "downloadable": downloadable
        })

        if "error" in result:
            logger.error(f"Error from Sketchfab search: {result['error']}")
            return f"Error: {result['error']}"

        # Safely get results with fallbacks for None
        if result is None:
            logger.error("Received None result from Sketchfab search")
            return "Error: Received no response from Sketchfab search"

        # Format the results
        models = result.get("results", []) or []
        if not models:
            return f"No models found matching '{query}'"

        formatted_output = f"Found {len(models)} models matching '{query}':\n\n"

        for model in models:
            if model is None:
                continue

            model_name = model.get("name", "Unnamed model")
            model_uid = model.get("uid", "Unknown ID")
            formatted_output += f"- {model_name} (UID: {model_uid})\n"

            # Get user info with safety checks
            user = model.get("user") or {}
            username = user.get("username", "Unknown author") if isinstance(user, dict) else "Unknown author"
            formatted_output += f"  Author: {username}\n"

            # Get license info with safety checks
            license_data = model.get("license") or {}
            license_label = license_data.get("label", "Unknown") if isinstance(license_data, dict) else "Unknown"
            formatted_output += f"  License: {license_label}\n"

            # Add face count and downloadable status
            face_count = model.get("faceCount", "Unknown")
            is_downloadable = "Yes" if model.get("isDownloadable") else "No"
            formatted_output += f"  Face count: {face_count}\n"
            formatted_output += f"  Downloadable: {is_downloadable}\n\n"

        return formatted_output
    except Exception as e:
        logger.error(f"Error searching Sketchfab models: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return f"Error searching Sketchfab models: {str(e)}"

@mcp.tool()
def get_sketchfab_model_preview(
    ctx: Context,
    uid: str
) -> Image:
    """
    Get a preview thumbnail of a Sketchfab model by its UID.
    Use this to visually confirm a model before downloading.

    Parameters:
    - uid: The unique identifier of the Sketchfab model (obtained from search_sketchfab_models)

    Returns the model's thumbnail as an Image for visual confirmation.
    """
    try:
        blender = get_blender_connection()
        logger.info(f"Getting Sketchfab model preview for UID: {uid}")

        result = blender.send_command("get_sketchfab_model_preview", {"uid": uid})

        if result is None:
            raise Exception("Received no response from Blender")

        if "error" in result:
            raise Exception(result["error"])

        # Decode base64 image data
        image_data = base64.b64decode(result["image_data"])
        img_format = result.get("format", "jpeg")

        # Log model info
        model_name = result.get("model_name", "Unknown")
        author = result.get("author", "Unknown")
        logger.info(f"Preview retrieved for '{model_name}' by {author}")

        return Image(data=image_data, format=img_format)

    except Exception as e:
        logger.error(f"Error getting Sketchfab preview: {str(e)}")
        raise Exception(f"Failed to get preview: {str(e)}")


@mcp.tool()
def download_sketchfab_model(
    ctx: Context,
    uid: str,
    target_size: float
) -> str:
    """
    Download and import a Sketchfab model by its UID.
    The model will be scaled so its largest dimension equals target_size.

    Parameters:
    - uid: The unique identifier of the Sketchfab model
    - target_size: REQUIRED. The target size in Blender units/meters for the largest dimension.
                  You must specify the desired size for the model.
                  Examples:
                  - Chair: target_size=1.0 (1 meter tall)
                  - Table: target_size=0.75 (75cm tall)
                  - Car: target_size=4.5 (4.5 meters long)
                  - Person: target_size=1.7 (1.7 meters tall)
                  - Small object (cup, phone): target_size=0.1 to 0.3

    Returns a message with import details including object names, dimensions, and bounding box.
    The model must be downloadable and you must have proper access rights.
    """
    try:
        blender = get_blender_connection()
        logger.info(f"Downloading Sketchfab model: {uid}, target_size={target_size}")

        result = blender.send_command("download_sketchfab_model", {
            "uid": uid,
            "normalize_size": True,  # Always normalize
            "target_size": target_size
        })

        if result is None:
            logger.error("Received None result from Sketchfab download")
            return "Error: Received no response from Sketchfab download request"

        if "error" in result:
            logger.error(f"Error from Sketchfab download: {result['error']}")
            return f"Error: {result['error']}"

        if result.get("success"):
            imported_objects = result.get("imported_objects", [])
            object_names = ", ".join(imported_objects) if imported_objects else "none"

            output = f"Successfully imported model.\n"
            output += f"Created objects: {object_names}\n"

            # Add dimension info if available
            if result.get("dimensions"):
                dims = result["dimensions"]
                output += f"Dimensions (X, Y, Z): {dims[0]:.3f} x {dims[1]:.3f} x {dims[2]:.3f} meters\n"

            # Add bounding box info if available
            if result.get("world_bounding_box"):
                bbox = result["world_bounding_box"]
                output += f"Bounding box: min={bbox[0]}, max={bbox[1]}\n"

            # Add normalization info if applied
            if result.get("normalized"):
                scale = result.get("scale_applied", 1.0)
                output += f"Size normalized: scale factor {scale:.6f} applied (target size: {target_size}m)\n"

            return output
        else:
            return f"Failed to download model: {result.get('message', 'Unknown error')}"
    except Exception as e:
        logger.error(f"Error downloading Sketchfab model: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return f"Error downloading Sketchfab model: {str(e)}"


# Main execution

def main():
    """Run the MCP server"""
    mcp.run()

if __name__ == "__main__":
    main()
