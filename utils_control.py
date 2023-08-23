import cv2
import pybullet as p
import pybullet_data
import math
import time
import numpy as np
import networkx as nx
import matplotlib.pyplot as plt
import random
import sys
import os
from matplotlib.colors import LinearSegmentedColormap

"""
PID controller
"""


class PIDController:
    def __init__(self, Kp, Ki, Kd, setpoint):
        """
        Initialize the PID controller with the given parameters. It is assumed
        that the setpoint is a function of the PID controller and that the
        parameters are in the form of numpy arrays.
        Args:
            Kp: The parameter matrix for the Poisson process.
            Ki: The parameter matrix for the Integral process.
            Kd: The parameter matrix for the Dirichlet process.
            setpoint: The target value for the PID controller.
        """
        # Initialize the PID controller with the given parameters
        self.Kp = Kp
        self.Ki = Ki
        self.Kd = Kd
        self.setpoint = setpoint

        # Initialize the variables used in the PID algorithm
        self.last_error = 0
        self.integral = 0

    def calculate(self, process_value):
        """
        Calculate the PID for a given process value.
        Args: process_value: The value of the process to be controlled.
        Returns: The output of the PID controller.
        """
        # Calculate the error, integral and derivative terms
        error = self.setpoint - process_value
        self.integral += error
        derivative = error - self.last_error
        # Calculate the PID output
        output = self.Kp * error + self.Ki * self.integral + self.Kd * derivative
        # Update the variables
        self.last_error = error
        return output

    def set_goal(self, setpoint):
        """
        Set the goal that will be used for this step.
        Args: setpoint: The Goal to be reached.
        """
        self.setpoint = setpoint


"""
Pose definition
"""


class Pose:
    # [x, y, z]: a point in a Cartesian coordinate system
    # [roll, pitch, yaw]: the orientation or heading of the robot
    def __init__(self, position_array, orientation_array):
        self.position = position_array
        self.x = position_array[0]
        self.y = position_array[1]
        self.z = position_array[2]

        self.orientation = orientation_array
        self.roll = orientation_array[0]
        self.pitch = orientation_array[1]
        self.yaw = orientation_array[2]


"""
Bestman API:
Segbot has 7 joints
Joint:0 and its name is: 'base_footprint_joint'
Joint:1 and its name is: 'base_link_to_cylinder_joint'
Joint:2 and its name is: 'cylinder_to_plate_joint'
Joint:3 and its name is: 'base_caster_support_joint'
Joint:4 and its name is: 'caster_wheel_joint'
Joint:5 and its name is: 'left_wheel_joint'
Joint:6 and its name is: 'right_wheel_joint'

UR5e has 7 joints
Joint:0 and its name is: 'shoulder_pan_joint'
Joint:1 and its name is: 'shoulder_lift_joint'
Joint:2 and its name is: 'elbow_joint'
Joint:3 and its name is: 'wrist_1_joint'
Joint:4 and its name is: 'wrist_2_joint'
Joint:5 and its name is: 'wrist_3_joint'
Joint:6 and its name is: 'ee_fixed_joint'
"""


class Bestman:
    def __init__(self, init_pos):
        # constant parameters
        self.client = p.connect(p.GUI)  # p.GUI or p.DIRECT
        # p.configureDebugVisualizer(p.COV_ENABLE_GUI, 0)
        p.setAdditionalSearchPath(pybullet_data.getDataPath())
        p.setGravity(0, 0, -9.8)
        p.setPhysicsEngineParameter(
            numSolverIterations=1000
        )  # Set the number of constraint solver iterations; Higher values increase precision but also increase computation time
        self.plane_id = p.loadURDF("plane.urdf")
        # other parameters
        self.line_visual = None  # for plotting
        # parameters for base
        self.frequency = 240  # simulation step for base and arm
        self.timeout = 100.0  # maximum time for planning
        # parameters for arm
        self.end_effector_index = 8
        self.max_force = 500
        self.max_iterations = 10000
        self.residual_threshold = 0.01
        self.max_attempts = 500

        # Initialize PID controller
        self.target_distance = 0.0
        self.controller = PIDController(
            Kp=0.01, Ki=0.0, Kd=0.0, setpoint=self.target_distance
        )
        self.rotated = False

        # Initialize base
        self.base_id = p.loadURDF(
            fileName="./URDF_robot/segbot.urdf",
            basePosition=init_pos.position,
            baseOrientation=p.getQuaternionFromEuler([0, 0, init_pos.yaw]),
        )

        # Initialize arm
        self.arm_id = p.loadURDF(
            # fileName="./URDF_robot/ur5e.urdf",
            # fileName="./URDF_robot/ur5e_2f85.urdf",  # TODO CHECK
            fileName="./URDF_robot/ur5e_vacuum.urdf",
            basePosition=init_pos.position,
            baseOrientation=p.getQuaternionFromEuler([0.0, 0.0, math.pi / 2.0]),
            useFixedBase=True,
        )

        # Add constraint between base and arm
        robot2_start_pos = [0, 0, 0]
        fixed_joint = p.createConstraint(
            self.base_id,
            -1,
            self.arm_id,
            -1,
            p.JOINT_FIXED,
            [0, 0, 0],
            [0, 0, 1.3],
            robot2_start_pos,
        )

        self.init_pos = init_pos
        self.gripper_id = None
        self.sync_base_arm_pose()
        self.set_visual_shape()

        # Obstacles in the environment
        self.obstacle_navigation_ids = []  # for navigation
        self.obstacle_manipulation_ids = []  # for manipulation

    # ----------------------------------------------------------------
    # Visualization functions
    # ----------------------------------------------------------------
    """
    This function crops a given image around a specified center point and returns the cropped portion. The resulting cropped image will be a square with the provided size. If the cropping dimensions exceed the original image boundaries, the function ensures it stays within the original image's dimensions to prevent out-of-bounds access.

    Args:
        image (np.array): The original image to be cropped. It is assumed to be a two-dimensional array, representing pixel values.
        center (tuple): A tuple (x, y) specifying the center point around which the image will be cropped.
        size (int): The side length of the resulting square cropped image.

    Returns:
        np.array: A cropped portion of the original image centered around the specified center point and with the given size.
    """

    def crop_image(self, image, center, size):
        image_height, image_width = image.shape
        top = max(0, int(center[1] - size / 2))
        bottom = min(image_height, int(center[1] + size / 2))
        left = max(0, int(center[0] - size / 2))
        right = min(image_width, int(center[0] + size / 2))
        return image[top:bottom, left:right]

    """
    This function captures and returns a cropped depth image from the PyBullet simulation. It positions the camera based on specified positions and orientations, captures the scene, extracts the depth information, and then crops the depth image around its center. Depth images are representations where pixel values indicate distances to the objects from the camera's perspective. Such images are valuable in robotics and computer vision applications for understanding the spatial relationships and distances between objects in a scene.
    Args:
        basePos (tuple): The target or base position in the scene towards which the camera is oriented. Typically, this could be the position of an object of interest, like a table.
        cameraPos (tuple): The position coordinates (x, y, z) where the camera is placed in the simulation environment.
        cameraUp (tuple): The upward direction vector of the camera, which determines the camera's orientation. For instance, (0, 1, 0) would point the camera's upward direction along the positive y-axis.

    Returns:
        np.array: A cropped depth image centered around the midpoint of the captured image, providing depth (distance) information from the camera's perspective.
    """

    def get_depth_image(self, basePos, cameraPos, cameraUp, enable_show=False, enable_save = False):
        viewMatrix = p.computeViewMatrix(cameraPos, basePos, cameraUp)
        projectionMatrix = p.computeProjectionMatrixFOV(
            fov=60, aspect=1.0, nearVal=0.01, farVal=100.0
        )

        img_arr = p.getCameraImage(
            640, 480, viewMatrix, projectionMatrix, shadow=1, lightDirection=[1, 1, 1]
        )
        depth_buffer = img_arr[3]  # depth buffer
        near = 0.01  # Near plane distance
        far = 100  # Far plane distance
        depth = far * near / (far - (far - near) * depth_buffer)
        # personalized colormap
        cdict = {
            "red": [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]],
            "green": [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]],
            "blue": [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]],
        }
        custom_cmap = LinearSegmentedColormap("custom_cmap", cdict)
        image_point = [320, 240]

        crop_size = 200
        depth_image = self.crop_image(depth, image_point, crop_size)

        print("depth size:{} * {}".format(len(depth_image), len(depth_image[0])))
        print("image_point:{}".format(image_point))

        if enable_show:
            plt.imshow(depth_image, cmap=custom_cmap)
            plt.colorbar()
            plt.show()
        
        if enable_save:
            plt.imsave('depth_image.png', depth_image, cmap=custom_cmap)

        return depth_image

    """
    This function is to enable and disable recording.
    """

    def start_record(self, fileName):
        logId = p.startStateLogging(
            p.STATE_LOGGING_VIDEO_MP4, "./image/" + fileName + ".mp4"
        )
        print("The video can be found in " + "./image/" + fileName + ".mp4")
        return logId

    def end_record(self, logId):
        p.stopStateLogging(logId)

    """
    This function draws an Axis-Aligned Bounding Box (AABB) around the specified table object in the simulation. The AABB is a box that covers the entire object based on its maximum and minimum coordinates along each axis. It can be useful for various purposes, such as collision detection, spatial partitioning, and bounding volume hierarchies.

    Args:
        table_id: The unique identifier of the table object in the simulation for which the AABB is to be drawn.
    """

    def draw_aabb(self, object_id):
        link_ids = [i for i in range(-1, p.getNumJoints(object_id))]
        for link_id in link_ids:
            aabb = p.getAABB(object_id, link_id)
            aabb_min = aabb[0]
            aabb_max = aabb[1]
            corners = [
                [aabb_min[0], aabb_min[1], aabb_min[2]],  # 0
                [aabb_max[0], aabb_min[1], aabb_min[2]],  # 1
                [aabb_max[0], aabb_max[1], aabb_min[2]],  # 2
                [aabb_min[0], aabb_max[1], aabb_min[2]],  # 3
                [aabb_min[0], aabb_min[1], aabb_max[2]],  # 4
                [aabb_max[0], aabb_min[1], aabb_max[2]],  # 5
                [aabb_max[0], aabb_max[1], aabb_max[2]],  # 6
                [aabb_min[0], aabb_max[1], aabb_max[2]],  # 7
            ]
            lines = [
                (0, 1),
                (1, 2),
                (2, 3),
                (3, 0),  # bottom face
                (4, 5),
                (5, 6),
                (6, 7),
                (7, 4),  # top face
                (0, 4),
                (1, 5),
                (2, 6),
                (3, 7),  # vertical edges
            ]
            color = [1, 0, 0]
            for line in lines:
                p.addUserDebugLine(
                    lineFromXYZ=corners[line[0]],
                    lineToXYZ=corners[line[1]],
                    lineColorRGB=color,
                    lineWidth=2,
                )

    """
    This method sets the visual color of the base and the arm of a robot in the simulation.
    """

    def set_visual_shape(self):
        """
        Set the color of base.
        """
        white = [1, 1, 1, 1]
        grey = [0.5, 0.5, 0.5, 1]
        for i in range(15):
            p.changeVisualShape(
                objectUniqueId=self.base_id, linkIndex=i, rgbaColor=white
            )
        p.changeVisualShape(objectUniqueId=self.base_id, linkIndex=4, rgbaColor=grey)
        p.changeVisualShape(objectUniqueId=self.base_id, linkIndex=5, rgbaColor=grey)
        p.changeVisualShape(objectUniqueId=self.base_id, linkIndex=6, rgbaColor=grey)

        """
        Set the color of arm.
        """
        blue = [0.53, 0.81, 0.92, 1.0]
        white = [1, 1, 1, 1]
        for i in range(15):
            p.changeVisualShape(
                objectUniqueId=self.arm_id, linkIndex=i, rgbaColor=white
            )
        p.changeVisualShape(objectUniqueId=self.arm_id, linkIndex=0, rgbaColor=blue)
        p.changeVisualShape(objectUniqueId=self.arm_id, linkIndex=3, rgbaColor=blue)
        p.changeVisualShape(objectUniqueId=self.arm_id, linkIndex=6, rgbaColor=blue)

    def visualize_path(self, path):
        # Reverse the path so that it goes from start to goal
        path = path[::-1]  # This line reverses the path
        cartesian_path = [self.joints_to_cartesian(point) for point in path]
        # print("cartesian_path:{}".format(cartesian_path))
        for i in range(len(cartesian_path) - 1):
            p.addUserDebugLine(
                cartesian_path[i],
                cartesian_path[i + 1],
                lineColorRGB=[1, 0, 0],
                lifeTime=10,
                lineWidth=3,
            )

    """
    This function sets the debug visualizer camera in a vertical view. The vertical view can be useful in various simulation scenarios like overhead view, bird's eye view etc. PyBullet Physics engine's debug visualizer camera is adjusted using this function.
    Args:
        height: The distance of the camera from the target point. This determines how far the camera is placed from the position (0, 0, 0). This value effectively becomes the "height" of the camera, providing a vertical overview.
    """

    def enable_vertical_view(self, height, position):
        p.resetDebugVisualizerCamera(
            cameraDistance=height,
            cameraYaw=0,
            cameraPitch=-89.9,
            cameraTargetPosition=[0, 0, 0],
            physicsClientId=self.client,
        )

    """
    This function draw a line on the screen from the specified start position to the target position.
    Args:
        start_pos: The starting position of the line as a tuple of (x, y, z) coordinates.
        target_pos: The ending position of the line as a tuple of (x, y, z) coordinates.
        color: A list representing the RGB values of the line's color. Default is red [1, 0, 0].
        width: The width of the line. Default is 3.0.
    """

    def draw_line(self, start_pos, target_pos, color=[1, 0, 0], width=3.0):
        if self.line_visual is not None:
            p.removeUserDebugItem(self.line_visual)
        self.line_visual = p.addUserDebugLine(
            start_pos, target_pos, lineColorRGB=color, lineWidth=width
        )

    # ----------------------------------------------------------------
    # Other functions
    # ----------------------------------------------------------------
    def disconnect_pybullet(self):
        p.disconnect()

    def wait(self, x):
        time.sleep(x)

    def run(self, x):
        for _ in range(x):
            p.stepSimulation()
            time.sleep(1.0 / self.frequency)

    # ----------------------------------------------------------------
    # Add object functions
    # ----------------------------------------------------------------

    """
    This function loads a given object into the PyBullet simulation environment. 
    The object is specified by a URDF (Unified Robot Description Format) file, which is a common format for representing a robot or other complex object in a simulation.

    Args:
        model_path (str): The path to the URDF file for the object.
        object_position (list): The initial position of the object in the simulation, as a three-element list or tuple of x, y, z coordinates.
        object_orientation (list): The initial orientation of the object in the simulation, as a three-element list or tuple of roll, pitch, and yaw angles (in radians). This will be converted to a quaternion for use with PyBullet.
        obj_name (str): The name of the object. This is used to create an attribute in the class instance that holds the ID of the object in the PyBullet simulation.

    Returns:
        The ID of the loaded object in the PyBullet simulation.
    """

    def load_object(
        self, model_path, object_position, object_orientation, scale, obj_name
    ):
        object_orientation = p.getQuaternionFromEuler(object_orientation)
        setattr(
            self,
            f"{obj_name}_id",
            p.loadURDF(
                model_path,
                basePosition=object_position,
                baseOrientation=object_orientation,
                globalScaling=scale,
            ),
        )
        print(
            "-" * 20
            + "\n"
            + "{}_id: {}".format(obj_name, getattr(self, f"{obj_name}_id"))
        )
        # TODO: can decompose this part out of bestman class to make it more general? make to an env build class?
        self.obstacle_navigation_ids.append(getattr(self, f"{obj_name}_id"))
        self.obstacle_manipulation_ids.append(getattr(self, f"{obj_name}_id"))
        time.sleep(1.0 / self.frequency)
        return getattr(self, f"{obj_name}_id")

    # ----------------------------------------------------------------
    # Get info from environment
    # ----------------------------------------------------------------
    def generate_point_within_area(self, min_x, min_y, max_x, max_y):
        x = random.uniform(min_x, max_x)
        y = random.uniform(min_y, max_y)
        return x, y

    def generate_point_within_area_with_fixed_z(
        self, min_x, min_y, max_x, max_y, fixed_z
    ):
        x = random.uniform(min_x, max_x)
        y = random.uniform(min_y, max_y)
        return x, y, fixed_z

    def generate_point_outside_area(self, min_x, min_y, max_x, max_y):
        range_x_min = -5
        range_x_max = 5
        range_y_min = -5
        range_y_max = 5

        regions = [
            (range_x_min, range_y_min, min_x, range_y_max),  # Left of table
            (max_x, range_y_min, range_x_max, range_y_max),  # Right of table
            (range_x_min, range_y_min, range_x_max, min_y),  # Below table
            (range_x_min, max_y, range_x_max, range_y_max),  # Above table
        ]

        # Randomly choose a region
        chosen_region = random.choice(regions)

        x = random.uniform(chosen_region[0], chosen_region[2])
        y = random.uniform(chosen_region[1], chosen_region[3])

        return x, y

    """
    This function retrieves the bounding box for a given object in the PyBullet simulation environment. 

    Args:
        object_id (int): The ID of the object in the PyBullet simulation.
    Prints:
        The function prints the minimum and maximum x, y, z coordinates of the bounding box of the object.
    """

    def get_bounding_box(self, object_id):
        link_ids = [i for i in range(-1, p.getNumJoints(object_id))]
        min_x, min_y, min_z = float("inf"), float("inf"), float("inf")
        max_x, max_y, max_z = float("-inf"), float("-inf"), float("-inf")
        for link_id in link_ids:
            (x_min, y_min, z_min), (x_max, y_max, z_max) = p.getAABB(object_id, link_id)
            min_x = min(min_x, x_min)
            min_y = min(min_y, y_min)
            min_z = min(min_z, z_min)
            max_x = max(max_x, x_max)
            max_y = max(max_y, y_max)
            max_z = max(max_z, z_max)
        # print("-" * 20 + "\n" + "object_id: {}".format(object_id))
        # print("min_x:{:.2f}, min_y:{:.2f}, min_z:{:.2f}".format(min_x, min_y, min_z))
        # print("max_x:{:.2f}, max_y:{:.2f}, max_z:{:.2f}".format(max_x, max_y, max_z))
        return [min_x, min_y, min_z, max_x, max_y, max_z]

    """
    This function checks if two bounding boxes collide.
    Each box is defined as [min_x, min_y, min_z, max_x, max_y, max_z].
        :param box1: bounding box of the first object.
        :param box2: bounding box of the second object.
        :return: True if they collide, False otherwise.
    """

    def check_collision_xyz(self, box1, box2):
        # Check for collision along the x-axis
        if box1[0] > box2[3] or box1[3] < box2[0]:
            return False

        # Check for collision along the y-axis
        if box1[1] > box2[4] or box1[4] < box2[1]:
            return False

        # Check for collision along the z-axis
        if box1[2] > box2[5] or box1[5] < box2[2]:
            return False

        return True

    def check_collision_xy(self, box1, box2):
        # Check for collision along the x-axis
        if box1[0] > box2[3] or box1[3] < box2[0]:
            return False

        # Check for collision along the y-axis
        if box1[1] > box2[4] or box1[4] < box2[1]:
            return False

        return True

    # ----------------------------------------------------------------
    # Segbot Navigation
    # ----------------------------------------------------------------

    """
    This function retrieves and prints the information about all joints of a specific object in the PyBullet simulation environment.

    Prints:
        The function first prints the total number of joints in the base model, and then it prints the index and name of each joint.
    """

    def get_base_joint_info(self):
        num_joints = p.getNumJoints(self.base_id)
        print("Segbot has {} joints".format(num_joints))
        for i in range(num_joints):
            joint_info = p.getJointInfo(self.base_id, i)
            joint_name = joint_info[1]
            joint_state = p.getJointState(self.base_id, i)
            joint_angle = joint_state[0]
            print(
                "Joint index:{}, name:{}, angle:{}".format(i, joint_name, joint_angle)
            )

    def get_link_names(self):
        num_joints = p.getNumJoints(self.base_id)
        for i in range(num_joints):
            joint_info = p.getJointInfo(self.base_id, i)
            link_name = joint_info[12].decode("UTF-8")
            print("Link index: {}, name: {}".format(i, link_name))

    """
    This function navigates a robot from its current position to a specified goal position. 
    The navigation is performed by finding a path from the current position to the goal.
    And then move along this path. 
    The robot's orientation is adjusted at each waypoint and finally set to the desired yaw angle at the goal.

    Args:
        goal_base_pose (Pose): The target pose (position and orientation) for the robot. 
    """

    def navigate_base(self, goal_base_pose):
        init_base_pose = self.get_base_pose()  # get current base pose
        path = self.find_base_path(
            init_base_pose, goal_base_pose
        )  # get a set of waypoints
        for waypoint in path:
            self.move_base_to_next_waypoint(
                Pose([waypoint[0], waypoint[1], 0], goal_base_pose.orientation)
            )  # move to each waypoint
        self.rotate_base(goal_base_pose.yaw)
        print("-" * 20 + "\n" + "navigation is done!")

    """
    This function retrieves the current position and orientation of a specific object

    Returns:
        The function returns a Pose object representing the current pose of the base. 
        The pose contains the position as a list [x, y, z] and the orientation as a list [roll, pitch, yaw].
    """

    def get_base_pose(self):
        base_position, base_orientation = p.getBasePositionAndOrientation(self.base_id)
        base_orientation = p.getEulerFromQuaternion(base_orientation)
        return Pose(base_position, base_orientation)

    """
    This function finds a path from a specified initial position to a goal position in a 2D grid representation.

    Args:
        init_base_position (Pose): The initial position of the robot.
        goal_base_position (Pose): The goal position of the robot.

    Returns:
        The function returns a list of waypoints that form a path from the initial position to the goal position. 
        Each waypoint is a list [x, y] representing a position in the world coordinates.
    
    Note:
        A* for path searching in map: 10 meter * 10 meter
        resolution is 0.1 meter
        a 2D grid: 1 for obstacles
    """

    def find_base_path(self, init_base_position, goal_base_position):
        init_base_position = init_base_position.position[0:2]  # only care about x, y
        goal_base_position = goal_base_position.position[0:2]

        def to_grid_coordinates(point, resolution):
            return [int(round(coordinate / resolution)) for coordinate in point]

        def to_world_coordinates(point, resolution):
            return [coordinate * resolution for coordinate in point]

        # Defining the environment size (in meters) and resolution
        size = 10
        resolution = 0.1
        n_points = int(size / resolution)

        # Create a 2D grid representing the environment
        static_map = np.zeros((n_points, n_points))

        # Add obstacles to the map
        for obstacle_id in self.obstacle_navigation_ids:
            aabb = self.get_bounding_box(obstacle_id)
            for i in range(int(aabb[0] / resolution), int(aabb[3] / resolution)):
                for j in range(int(aabb[1] / resolution), int(aabb[4] / resolution)):
                    static_map[i][j] = 1

        # plt.imshow(static_map, cmap='gray_r')  # 'gray_r' means reversed grayscale
        # plt.show()

        init_base_position = to_grid_coordinates(
            init_base_position, resolution
        )  # In grid coordinates
        goal_base_position = to_grid_coordinates(
            goal_base_position, resolution
        )  # In grid coordinates

        # Make sure the positions are within the environment and not on the table
        assert (
            0 <= init_base_position[0] < n_points
            and 0 <= init_base_position[1] < n_points
        ), "Initial base position is out of boundary!"
        assert (
            0 <= goal_base_position[0] < n_points
            and 0 <= goal_base_position[1] < n_points
        ), "Goal base position is out of boundary!"
        assert (
            static_map[init_base_position[0], init_base_position[1]] != 1
        ), "Initial base position is in an obstacle!"
        assert (
            static_map[goal_base_position[0], goal_base_position[1]] != 1
        ), "Goal base position is in an obstacle!"

        # Convert the numpy grid map to NetworkX graph
        graph = nx.grid_2d_graph(*static_map.shape)
        for i in range(n_points):
            for j in range(n_points):
                if static_map[i, j] == 1:
                    graph.remove_node((i, j))

        path = nx.astar_path(
            graph, tuple(init_base_position), tuple(goal_base_position)
        )

        # Convert grid coordinates back to world coordinates
        path = [to_world_coordinates(point, resolution) for point in path]
        # print('raw path:{}'.format(path))
        return path

    """
    This function moves the base of a robot to the next waypoint (next_waypoint). 
    The robot first rotates towards the target, and then moves towards it in a straight line. 
    The movement is controlled by a controller (assumed to be a PID controller) that adjusts the velocity of the robot based on the distance to the target.

    Args:
        next_waypoint (Pose): The target pose (position and orientation) for the robot. This should be an instance of a Pose class, which is assumed to have 'x' and 'y' properties.
    """

    def move_base_to_next_waypoint(self, next_waypoint):
        self.next_waypoint = next_waypoint
        self.target_distance = 0.0
        self.rotated = False
        while True:
            pose = self.get_base_pose()
            target = self.next_waypoint
            x, y = pose.x, pose.y

            distance = math.sqrt((x - target.x) ** 2 + (y - target.y) ** 2)
            yaw = math.atan2(target.y - y, target.x - x)

            self.controller.set_goal(self.target_distance)
            output = self.controller.calculate(distance)

            if not self.rotated:
                # print('Step into orientation changing')
                self.rotate_base(yaw)
                # print('Reorienting finished!')
                self.rotated = True

            THRESHOLD = 0.01
            if distance < THRESHOLD:
                output = 0.0
                # print('Reach goal: {}!'.format(self.next_waypoint.position))
                pose = self.get_base_pose()
                break

            self.action(-output)

    """
    This function rotates the base of a robot to a specified yaw angle. 

    Args:
        base_yaw (float): The target yaw angle (in radians) for the base.
    """

    def rotate_base(self, base_yaw):
        # Convert a Euler yaw angle (in radians) to Quaternion.
        def angle_to_quaternion(base_yaw):
            return [0, 0, math.sin(base_yaw / 2.0), math.cos(base_yaw / 2.0)]

        position, orientation = p.getBasePositionAndOrientation(self.base_id)
        orientation = angle_to_quaternion(base_yaw)
        p.resetBasePositionAndOrientation(self.base_id, position, orientation)

        if self.arm_id is not None:
            self.sync_base_arm_pose()
        p.stepSimulation()
        time.sleep(1.0 / self.frequency)

    """
    This function uses the output of the PID controller to adjust the position of the robot's base.

    Args:
        output (float): The output of the PID controller, which is used to calculate the new position of the robot's base.
    """

    def action(self, output):
        position, orientation = p.getBasePositionAndOrientation(self.base_id)
        euler_angles = p.getEulerFromQuaternion(orientation)

        delta_x = output * math.cos(euler_angles[2])
        delta_y = output * math.sin(euler_angles[2])

        target_position = [position[0] + delta_x, position[1] + delta_y, position[2]]
        target_orientation = orientation

        result = self.check_collision_navigation()  # check if collision exists
        # if result:
        #     assert "Collision detected during navigation, stopping..."

        p.resetBasePositionAndOrientation(
            self.base_id, target_position, target_orientation
        )
        self.sync_base_arm_pose()
        time.sleep(1.0 / self.frequency)

    """
    This function synchronizes the pose (position and orientation) of the robot's arm with its base.
    """

    def sync_base_arm_pose(self):
        position, orientation = p.getBasePositionAndOrientation(self.base_id)
        position = [position[0], position[1], 1.02]  # fixed height
        p.resetBasePositionAndOrientation(self.arm_id, position, orientation)

    """
    This function check if collision exists during navigation
    """

    def check_collision_navigation(self):
        for obstacle_id in self.obstacle_navigation_ids:
            aabb_base = self.get_bounding_box(self.base_id)
            aabb_obstacle = self.get_bounding_box(obstacle_id)
            if self.check_collision_xy(
                aabb_base, aabb_obstacle
            ):  # print("Collision detected during navigation, stopping...")
                return True
        return False

    # ----------------------------------------------------------------
    # Arm Manipulation
    # ----------------------------------------------------------------

    """
    This function retrieves and prints information about the joints of the robot's arm.
    """

    def get_arm_joint_info(self):
        num_joints = p.getNumJoints(self.arm_id)
        print("UR5e has {} joints".format(num_joints))
        for i in range(num_joints):
            joint_info = p.getJointInfo(self.arm_id, i)
            joint_name = joint_info[1]
            joint_state = p.getJointState(self.arm_id, i)
            joint_angle = joint_state[0]
            print(
                "Joint index:{}, name:{}, angle:{}".format(i, joint_name, joint_angle)
            )

    """
    This function retrieves and return joints' angle.
    """

    def get_arm_joint_angle(self):
        joint_angles = []  # Initialize an empty list to store the joint angles
        for i in range(6):
            joint_state = p.getJointState(self.arm_id, i)
            joint_angle = joint_state[0]
            joint_angles.append(joint_angle)  # Add the current joint angle to the list
        return joint_angles  # Return the list of joint angles

    """
    This function commands the robot's arm to move to a specific set of joint angles.

    Args:
        joint_angles: A list of desired joint angles (in radians) for each joint of the arm.
    """

    def set_arm_to_joint_angles(self, joint_angles):
        for joint_index in range(6):
            p.resetJointState(
                bodyUniqueId=self.arm_id,
                jointIndex=joint_index,
                targetValue=joint_angles[joint_index],
            )
        p.stepSimulation()
        time.sleep(1.0 / self.frequency)

    """
    This function commands the robot's arm to move to a specific set of joint angles.

    Args:
        joint_angles: A list of desired joint angles (in radians) for each joint of the arm.
    """

    def move_arm_to_joint_angles(self, joint_angles):
        num_joints = len(joint_angles)
        assert (
            p.getNumJoints(self.arm_id) >= num_joints
        ), "Invalid number of joint angles."

        # set arm's target joints
        for joint_index in range(num_joints):
            p.setJointMotorControl2(
                bodyUniqueId=self.arm_id,
                jointIndex=joint_index,
                controlMode=p.POSITION_CONTROL,
                targetPosition=joint_angles[joint_index],
                force=self.max_force,
            )

        start_time = time.time()

        while True:
            p.stepSimulation()
            time.sleep(1.0 / self.frequency)

            # Check if reach the goal
            current_angles = [
                p.getJointState(self.arm_id, i)[0] for i in range(num_joints)
            ]
            diff_angles = [abs(a - b) for a, b in zip(joint_angles, current_angles)]
            if all(diff < self.residual_threshold for diff in diff_angles):
                # print(
                #     "-" * 20
                #     + "\n"
                #     + "Reached target joint position:{}".format(joint_angles)
                # )
                break

            # Check for timeout
            if time.time() - start_time > self.timeout:
                print(
                    "-" * 20 + "\n" + "Timeout before reaching target joint position."
                )
                break

    """
    This function retrieves the information about the end effector of the robot arm.
    """

    def get_end_effector_info(self):
        joint_info = p.getJointInfo(self.arm_id, self.end_effector_index)
        end_effector_name = joint_info[1].decode("utf-8")
        end_effector_info = p.getLinkState(
            bodyUniqueId=self.arm_id, linkIndex=self.end_effector_index
        )
        end_effector_position = end_effector_info[0]
        end_effector_orientation = end_effector_info[1]
        # print("-" * 20 + "\n" + "End effector name:{}".format(end_effector_name))
        # print("Its position:{}".format(end_effector_position))
        # print("Its orientation:{}".format(end_effector_orientation))

    def joints_to_cartesian(self, joint_angles):
        self.set_arm_to_joint_angles(joint_angles)

        end_effector_info = p.getLinkState(
            bodyUniqueId=self.arm_id, linkIndex=self.end_effector_index
        )
        orientation = p.getEulerFromQuaternion(end_effector_info[1])
        position = end_effector_info[0]
        # print("-" * 30 + "\n" + "position:{}".format(position))
        # print("orientation:{}".format(orientation))
        return position

    def cartesian_to_joints(self, position, orientation):
        joint_angles = p.calculateInverseKinematics(
            bodyUniqueId=self.arm_id,
            endEffectorLinkIndex=self.end_effector_index,
            targetPosition=position,
            targetOrientation=p.getQuaternionFromEuler(orientation),
            maxNumIterations=self.max_iterations,
            residualThreshold=self.residual_threshold,
        )
        return joint_angles

    """
    This function rotates the end effector of the robot arm by a specified angle.

    Args:
        angle (float): The desired rotation angle in radians.
    """

    def rotate_end_effector(self, angle):
        # Get the current joint states
        joint_indices = [
            i for i in range(6)
        ]  # Change this to your actual joint indices
        joint_states = p.getJointStates(self.arm_id, joint_indices)

        # Create a new list of target joint angles
        target_joint_angles = [joint_state[0] for joint_state in joint_states]

        # Add desired rotation to the last joint's current angle
        target_joint_angles[-1] += angle

        # Set the target angles
        self.set_arm_to_joint_angles(target_joint_angles)

        # Step the simulation until the joints reach their target angles
        while True:
            # Update the current joint states
            joint_states = p.getJointStates(self.arm_id, joint_indices)
            current_joint_angles = [joint_state[0] for joint_state in joint_states]

            # Check if all joints have reached their target angles
            if all(
                abs(current_joint_angles[i] - target_joint_angles[i]) < 0.01
                for i in range(6)
            ):
                break

            p.stepSimulation()
            time.sleep(1.0 / self.frequency)
        print("Rotation completed!")

    """
    This function moves the robot arm's end effector to a target position.

    Args:
        end_effector_goal_pose (Pose): The desired pose of the end effector (includes both position and orientation). 
    """

    def move_end_effector_to_goal_position(self, end_effector_goal_pose):
        # get current end effector position
        state = p.getLinkState(
            bodyUniqueId=self.arm_id, linkIndex=self.end_effector_index
        )
        current_position = state[0]

        # calculate distance
        distance = np.linalg.norm(
            np.array(end_effector_goal_pose.position) - np.array(current_position)
        )

        # check if the distance is small enough, if yes, return immediately
        if distance < self.residual_threshold:  # use an appropriate value here
            pass
            # print(
            #     "Current position is already close to target position. No need to move."
            # )
            # for _ in range(10):
            #     p.stepSimulation()
            #     time.sleep(1.0 / self.frequency)
            # return

        target_position = end_effector_goal_pose.position
        target_orientation = end_effector_goal_pose.orientation
        # target_orientation = [0, math.pi / 2.0, 0]  # vcertical downward grip

        attempts = 0
        while attempts < self.max_attempts:
            joint_angles = self.cartesian_to_joints(target_position, target_orientation)

            # If IK solution is invalid, break this loop and start a new attempt
            if joint_angles is None or len(joint_angles) != 6:
                break

            # Test the calculated angles for collision
            for joint_index, joint_angle in enumerate(joint_angles):
                p.resetJointState(self.arm_id, joint_index, joint_angle)

            p.stepSimulation()

            # if p.getContactPoints(self.arm_id): # collision with other objects
            if p.getContactPoints(self.arm_id, self.base_id):
                # Collision detected, break and re-calculate the IK
                break
            else:
                # get current end effector position
                state = p.getLinkState(
                    bodyUniqueId=self.arm_id, linkIndex=self.end_effector_index
                )
                current_position = state[0]

                # calculate error
                error = np.linalg.norm(
                    np.array(target_position) - np.array(current_position)
                )
                # print('current_position:{}, target_position:{}, error:{}'.format(current_position, target_position, error))

                if error < self.residual_threshold * 2:
                    self.move_arm_to_joint_angles(joint_angles)
                    # print(
                    #     "Reached target end effector position:{}".format(
                    #         end_effector_goal_pose.position
                    #     )
                    # )
                    return True
            attempts += 1
        print(
            "Could not reach target position without collision after {} attempts".format(
                self.max_attempts
            )
        )
        return False

    """
    This function performs pick-and-place manipulation of an object using the robot arm.

    Args:
        object_id: The ID of the target object to be manipulated.
        object_goal_position: The goal position for the target object.
        object_goal_orientation: The goal orientation for the target object.
    """

    def pick_place(self, object_id, object_goal_position, object_goal_orientation):
        print("debug")
        # get target object position
        object_position_init, orientation = p.getBasePositionAndOrientation(object_id)
        target_position = [
            object_position_init[0],
            object_position_init[1],
            object_position_init[2] + 0.1,
        ]
        for step in range(1000):
            gripper_status = {"ungrasp": 0, "grasp": 1}
            gripper_value = gripper_status["ungrasp"]
            if step >= 150 and step < 250:
                target_position = [
                    object_position_init[0],
                    object_position_init[1],
                    object_position_init[2] + 0.1,
                ]  # grab object
                gripper_value = gripper_status["grasp"]
            elif step >= 250 and step < 400:
                target_position = [
                    object_position_init[0],
                    object_position_init[1],
                    object_position_init[2] + 0.2,
                ]
                gripper_value = gripper_status["grasp"]
            elif step >= 400 and step < 600:
                target_position = [
                    object_position_init[0]
                    + (object_goal_position[0] - object_position_init[0])
                    * (step - 400)
                    / 200,
                    object_position_init[1]
                    + (object_goal_position[1] - object_position_init[1])
                    * (step - 400)
                    / 200,
                    object_position_init[2] + 0.2,
                ]
                gripper_value = gripper_status["grasp"]
            elif step >= 600 and step < 800:
                target_position = [
                    object_goal_position[0],
                    object_goal_position[1],
                    object_goal_position[2] + 0.1,
                ]  # stop at target position
                gripper_value = gripper_status["grasp"]
            elif step >= 800:
                target_position = [
                    object_goal_position[0],
                    object_goal_position[1],
                    object_goal_position[2] + 0.1,
                ]  # drop object
                gripper_value = gripper_status["ungrasp"]

            self.move_end_effector_to_goal_position(
                Pose(target_position, object_goal_orientation)
            )

            if gripper_value == 0 and self.gripper_id != None:
                p.removeConstraint(self.gripper_id)
                self.gripper_id = None
                for _ in range(self.frequency):
                    p.stepSimulation()
            if gripper_value == 1 and self.gripper_id == None:
                cube_orn = p.getQuaternionFromEuler([0, math.pi, 0])
                self.gripper_id = p.createConstraint(
                    self.arm_id,
                    self.end_effector_index,
                    object_id,
                    -1,
                    p.JOINT_FIXED,
                    [0, 0, 0],
                    [0, 0, 0.05],
                    [0, 0, 0],
                    childFrameOrientation=cube_orn,
                )

            p.stepSimulation()

        print("-" * 20 + "\n" + "manipulation is done!")

    """
    This function calculates the inverse kinematics (IK) error for performing pick-and-place manipulation of an object using a robot arm.

    Args:
        goal_position: The desired goal position for the target object.
        goal_orientation: The desired goal orientation for the target object.
    """

    def calculate_IK_error(self, goal_position, goal_orientation):
        joint_angles = p.calculateInverseKinematics(
            bodyUniqueId=self.arm_id,
            endEffectorLinkIndex=self.end_effector_index,
            targetPosition=goal_position,
            targetOrientation=p.getQuaternionFromEuler(goal_orientation),
            maxNumIterations=self.max_iterations,
            residualThreshold=self.residual_threshold,
        )
        print("IK: joint_angles is {}".format(joint_angles))

        self.move_arm_to_joint_angles(joint_angles)

        state = p.getLinkState(
            bodyUniqueId=self.arm_id, linkIndex=self.end_effector_index
        )
        actual_position = state[0]
        print("actual position: {}".format(actual_position))

        target_position = np.array(goal_position)
        actual_position = np.array(actual_position)
        difference = np.linalg.norm(target_position - actual_position)
        print("IK error: {}".format(difference))

    # ----------------------------------------------------------------
    # For RL algorithm
    # ----------------------------------------------------------------

    def reset_pos(self, seed=None, pos=None):
        """
        Reset the robot to initial state when the pose is not given
        """
        if pos is None:
            # reset the base and arm to initial state when the pose is not given
            p.resetBasePositionAndOrientation(
                self.base_id,
                self.init_pos.position,
                p.getQuaternionFromEuler([0, 0, self.init_pos.yaw]),
            ),
            # BUG: need to reset the arm separately or just call the sync_base_arm_pose?
            self.sync_base_arm_pose()
        else:
            p.resetBasePositionAndOrientation(
                self.base_id,
                pos.position,
                p.getQuaternionFromEuler([0, 0, pos.yaw]),
            )
            self.sync_base_arm_pose()

    def get_current_pos(self):
        """
        Get the current position of the robot
        """
        base_position, base_orientation = p.getBasePositionAndOrientation(self.base_id)
        base_orientation = p.getEulerFromQuaternion(base_orientation)
        return Pose(base_position, base_orientation)

    def set_camera(
        self,
        robot_id: int,
        width: int = 224,
        height: int = 224,
    ):
        # get current position and orientation for robot
        base_position, orientation = p.getBasePositionAndOrientation(robot_id)

        # set camera position and target position
        base_position, orientation_quat = p.getBasePositionAndOrientation(self.base_id)
        camera_position = np.array(base_position) + np.array([0, 0, 2.5])
        tilt = 3
        rot_mat = np.array(p.getMatrixFromQuaternion(orientation_quat)).reshape(3, 3)
        forward_vec = rot_mat @ np.array(
            [tilt, 0, 0]
        )  # Use the rotation matrix to rotate the vector
        target_position = np.array(base_position) + forward_vec
        view_mat = p.computeViewMatrix(
            cameraEyePosition=camera_position,
            cameraTargetPosition=target_position,
            cameraUpVector=[0, 1, 0],
        )
        proj_mat = p.computeProjectionMatrixFOV(
            fov=60.0,  # field of view
            aspect=0.8,  # scale, default=1
            nearVal=0.01,  # view distance min
            farVal=10,
        )
        # get image from the camera
        w, h, rgb, depth, seg = p.getCameraImage(
            width=width,
            height=height,
            viewMatrix=view_mat,
            projectionMatrix=proj_mat,
            physicsClientId=self.client,
        )
        return w, h, rgb, depth, seg

    def get_image(self, width, height):
        return self.set_camera(self.base_id, width, height)

    """
        This function draws a rectangular box around the target object in the RGB image.

        Args:
        rgb_img (np.ndarray): The RGB image.
        seg_img (np.ndarray): The segmentation image.
        target_object_id (int): The ID of the target object in the segmentation image.
        file_output_path (str, optional): The path to save the output image. Default is './outputs/target_rectangle.png'.

        Returns:
        np.ndarray: The RGB image with a rectangular box drawn around the target object.
    """

    def label_target_on_rgb_by_segmentation(
        self,
        rgb_img,
        seg_img,
        height=224,
        width=224,
        target_object_id=4,
        file_output_path="./outputs/target_rectangle.png",
    ):
        # get the target object's bounding box
        target_object_mask = np.where(seg_img == target_object_id, 255, 0).astype(
            np.uint8
        )
        contours, _ = cv2.findContours(
            target_object_mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE
        )
        # if the target object is found, draw a rectangular box around it
        if len(contours) != 0:
            x, y, w, h = cv2.boundingRect(contours[0])
            # draw a rectangular box around the target object
            cv2.rectangle(rgb_img, (x, y), (x + w, y + h), (0, 255, 0), 2)
            # transfer the image to RGB from BGR
        rgb_img = cv2.cvtColor(rgb_img, cv2.COLOR_BGR2RGB)
        # save the image
        cv2.imwrite(file_output_path, rgb_img)
        return rgb_img

    def forward(self, distance=0.25):  # distance is given in meters
        # get the current pose
        current_pose = self.get_base_pose()
        current_position = current_pose.position
        current_orientation = current_pose.orientation

        # get the current yaw angle
        _, _, yaw = current_orientation

        # calculate the new position
        new_position = [
            current_position[0] + distance * math.cos(yaw),
            current_position[1] + distance * math.sin(yaw),
            current_position[2],
        ]

        # set the new position as a goal
        goal_pose = Pose(new_position, current_orientation)

        # move to the new position
        self.move_base_to_next_waypoint(goal_pose)

        result = self.check_collision_navigation()  # check if collision exists
        if result:
            print("Fail to move forward without collision.")
            return False
        print("Successfully moved forward without collision.")
        return True

    def rotate_left(self, degree=30):  # degree is given in degrees
        # Convert the degree to radians
        radian = math.radians(degree)

        # Get the current pose
        current_pose = self.get_base_pose()
        current_orientation = current_pose.orientation

        # Get the current yaw angle
        _, _, yaw = current_orientation

        # Calculate the new yaw angle
        new_yaw = yaw + radian  # Add radian to current yaw for a left turn

        # Ensure the yaw is within -pi to pi
        new_yaw = (new_yaw + math.pi) % (2 * math.pi) - math.pi

        # Rotate to the new yaw angle
        self.rotate_base(new_yaw)

        result = self.check_collision_navigation()  # check if collision exists
        if result:
            print("Fail to rotate left without collision.")
            return False
        print("Successfully rotate left without collision.")
        return True

    def rotate_right(self, degree=30):  # degree is given in degrees
        # Convert the degree to radians
        radian = math.radians(degree)

        # Get the current pose
        current_pose = self.get_base_pose()
        current_orientation = current_pose.orientation

        # Get the current yaw angle
        _, _, yaw = current_orientation

        # Calculate the new yaw angle
        new_yaw = yaw - radian  # Subtract radian from current yaw for a right turn

        # Ensure the yaw is within -pi to pi
        new_yaw = (new_yaw + math.pi) % (2 * math.pi) - math.pi

        # Rotate to the new yaw angle
        self.rotate_base(new_yaw)

        result = self.check_collision_navigation()  # check if collision exists
        if result:
            print("Fail to rotate right without collision.")
            return False
        print("Successfully rotate right without collision.")
        return True

    def stop(self):
        zero_velocity = [0, 0, 0]
        p.resetBaseVelocity(self.base_id, zero_velocity, zero_velocity)
        return True

    def grasp(self, object_id):
        # get target object position
        object_position_init, orientation = p.getBasePositionAndOrientation(object_id)

        # orientation = [0.0, math.pi / 2.0, 0.0]  # vertial downward grip
        orientation = [0.0, math.pi, 0.0]  # vertial downward grip

        target_position = [
            object_position_init[0],
            object_position_init[1],
            object_position_init[2] + 0.1,
        ]

        gripper_status = {"ungrasp": 0, "grasp": 1}
        gripper_value = gripper_status["ungrasp"]
        for step in range(100):
            if step >= 20:
                target_position = [
                    object_position_init[0],
                    object_position_init[1],
                    object_position_init[2] + 0.1,
                ]  # grab object
                gripper_value = gripper_status["grasp"]

            result = self.move_end_effector_to_goal_position(
                Pose(target_position, orientation)
            )
            if not result:
                print("-" * 20 + "\n" + "grasping is unsuccessful!")
                return False

            if gripper_value == 0 and self.gripper_id != None:
                p.removeConstraint(self.gripper_id)
                self.gripper_id = None
            if gripper_value == 1 and self.gripper_id == None:
                cube_orn = p.getQuaternionFromEuler([0, math.pi, 0])
                self.gripper_id = p.createConstraint(
                    self.arm_id,
                    self.end_effector_index,
                    object_id,
                    -1,
                    p.JOINT_FIXED,
                    [0, 0, 0],
                    [0, 0, 0.05],
                    [0, 0, 0],
                    childFrameOrientation=cube_orn,
                )

            p.stepSimulation()

        # check if the object has been moved
        object_position_after, _ = p.getBasePositionAndOrientation(object_id)
        if object_position_init != object_position_after:
            print("-" * 20 + "\n" + "grasping is successful!")
            return True
        else:
            print("-" * 20 + "\n" + "grasping is unsuccessful!")
            return False

    def set_envs(self):
        # load table
        table_id = self.load_object(
            "./URDF_models/furniture_table_rectangle_high/table.urdf",
            [1.0, 1.0, 0.0],
            [0.0, 0.0, 0.0],
            1.0,
            "table",
        )

        base_boundary = self.get_bounding_box(self.base_id)
        arm_boundary = self.get_bounding_box(self.arm_id)
        table_boundary = self.get_bounding_box(table_id)

        obstacle_boundary_list = []  # store obstacles
        obstacle_boundary_list.append(base_boundary)
        obstacle_boundary_list.append(arm_boundary)
        obstacle_boundary_list.append(table_boundary)

        # generate random position for bowl within the table boundary
        table_min_x, table_min_y, table_min_z, table_max_x, table_max_y, table_max_z = (
            table_boundary[0],
            table_boundary[1],
            table_boundary[2],
            table_boundary[3],
            table_boundary[4],
            table_boundary[5],
        )
        # print('debug: table_boundary:{}'.format(table_boundary))

        bowl_x = random.uniform(table_min_x, table_max_x)
        bowl_y = random.uniform(table_min_y, table_max_y)
        bowl_id = self.load_object(
            "./URDF_models/utensil_bowl_blue/model.urdf",
            [bowl_x, bowl_y, table_max_z + 0.05],  # bowl is on the table
            [0.0, 0.0, 0.0],
            1.0,
            "bowl",
        )

        # initialize chair_id list
        chair_ids = []

        # number of chairs to be loaded, random between 1 and 3
        num_chairs = random.randint(2, 3)

        for _ in range(num_chairs):
            collision_flag = True
            max_attempts = 200

            for attempt in range(max_attempts):
                chair_x, chair_y = self.generate_point_outside_area(
                    table_min_x, table_min_y, table_max_x, table_max_y
                )
                # print('chair_x:{}, chair_y:{}'.format(chair_x, chair_y))
                chair_size = 0.3
                chair_boundary = [
                    chair_x - chair_size,
                    chair_y - chair_size,
                    0.1,
                    chair_x + chair_size,
                    chair_y + chair_size,
                    1.0,
                ]

                # if the chair position is collision with other obstacle, break the loop
                collision_flag = any(
                    self.check_collision_xy(chair_boundary, obstacle)
                    for obstacle in obstacle_boundary_list
                )

                if not collision_flag:
                    chair_angle = random.uniform(0, 2 * math.pi)
                    chair_z = 0.1  # assuming the chair is on the floor
                    chair_id = self.load_object(
                        "./URDF_models/furniture_chair/model.urdf",
                        [chair_x, chair_y, chair_z],
                        [math.pi / 2.0 * 3, 0.0, chair_angle],
                        1.5,
                        "chair",
                    )
                    obstacle_boundary_list.append(chair_boundary)
                    chair_ids.append(chair_id)
                    break

            if collision_flag and attempt == max_attempts - 1:
                print("-" * 20 + "\n" + "Failed to place chair after maximum attempts!")

        # return IDs of all loaded objects
        return {"table": table_id, "bowl": bowl_id, "chairs": chair_ids}


class PbOMPL:
    def __init__(self, robot_id, joint_idx, obstacles=[], planner="RRT", threshold=0.1):
        """
        Initialize the OMPL.

        Args:
           robot_id: ID of the robot to use.
           joint_idx: Index of the joints to use in planning.
           obstacles: List of obstacles to consider in the motion planning.
           planner: The name of the motion planner algorithm to use.
           threshold: The threshold value ONLY for repalnning.
        """
        self.robot_id = robot_id
        self.threshold = threshold
        self.robot = pb_ompl.PbOMPLRobot(robot_id, joint_idx=joint_idx)
        self.obstacles = obstacles if obstacles is not None else []
        self.pb_ompl_interface = pb_ompl.PbOMPL(self.robot, self.obstacles)
        self.set_planner(planner)

        item_info = p.getBodyInfo(self.robot_id)
        robot_name = item_info[1].decode("utf-8")
        print("--------------------")
        print(
            f"OMPL Configuration\n"
            f"Robot ID: {robot_id}\n"
            f"Robot Name: {robot_name}\n"
            f"Obstacles: {obstacles}\n"
            f"Planner: {planner}\n"
            f"Threshold: {threshold}"
        )
        print("--------------------")

    def set_planner(self, planner):
        """
        Set Planner to OMPL.

        Args:
                planner: The planner to set to OMPL.
        """
        try:
            self.pb_ompl_interface.set_planner(planner)
        except Exception as e:
            print(f"Error setting planner: {e}")

    def set_target(self, target_id):
        """
        Set the target to be used for the manipulation task.

        Args:
                target_id: id of the target.
        """
        try:
            item_info = p.getBodyInfo(target_id)
        except Exception as e:
            print(f"Error setting target: {e}")
        self.target = target_id
        self.target_pos, _ = p.getBasePositionAndOrientation(self.target)

    def set_target_pos(self, target_pos):
        """
        Set the position of the target.

        Args:
                target_pos: The position of the target.
        """
        self.target_pos = target_pos

    def set_obstacles(self, obstacles):
        """
        Set obstacles to OMPL.

        Args:
                obstacles: List of obstacle.
        """
        for i in obstacles:
            try:
                item_info = p.getBodyInfo(i)
            except Exception as e:
                print(f"Error adding obstacle: {e}")
        self.obstacles = obstacles

    def add_obstacles(self, item_id):
        """
        Add obstacle to list of obstacles.

        Args:
                item_id: id of the item to add.
        """
        try:
            item_info = p.getBodyInfo(item_id)
        except Exception as e:
            print(f"Error adding obstacle: {e}")
        self.obstacles.append(item_id)

    def remove_obstacles(self, obstacle_id):
        """
        Remove obstacle from the list of obstacles.
        This is useful for removing a specific obstacle from the list.

        Args:
                obstacle_id: id of the obstacle to remove.
        """
        try:
            self.obstacles = [obs for obs in self.obstacles if obs != obstacle_id]
        except Exception as e:
            print(f"Error removing obstacle: {e}")

    def store_obstacles(self):
        """
        Store obstacles in the OMPL interface.
        This is called after the user finished obstacles setting.
        """
        self.pb_ompl_interface.set_obstacles(self.obstacles)

    def check_obstacles(self):
        """
        Check obstacles in the scene and print them to the console.
        This is a debugging function.
        """
        if self.obstacles == []:
            print("Obstacle list is empty")
        else:
            # print the IDs and names of all obstacles in the scene
            for obstacle_id in self.obstacles:
                item_info = p.getBodyInfo(obstacle_id)
                item_name = item_info[1].decode("utf-8")
                print(f"\t Obstacle Name: {item_name}, ID: {obstacle_id}")

    def get_scene_items(self, display=True):
        """
        Get IDs of all items in the scene.
        This is a debugging function.

        Args:
                display: If True ( default ) the ID will be displayed on the screen.

        Returns:
                A list of IDs of all items in the scene.
        """
        # Get the total number of items in the scene
        num_items = p.getNumBodies()
        # Initialize an empty list to store the IDs of all items in the scene
        all_item_ids = []

        # This function will return the list of all the items in the list
        for item_id in range(num_items):
            current_id = p.getBodyUniqueId(item_id)
            all_item_ids.append(current_id)
            if display:
                item_info = p.getBodyInfo(current_id)
                item_name = item_info[1].decode("utf-8")
                print(f"Item Name: {item_name}, ID: {current_id}")
        return all_item_ids

    def add_scene_obstacles(self, display=False):
        """
        Add obstacles to the scene.
        This is done by iterating over all items in the scene and adding them to the list of obstacles.

        Args:
                display: If True the name and ID of the item will be displayed on the screen.
            Default is False. If False it will not be displayed.

        Returns:
                A list of IDs of all items in the obstacle list.
        """
        all_item_ids = self.get_scene_items(display=False)
        self.obstacles = []
        # Add the current item ID to the list of obstacles
        for item_id in all_item_ids:
            # Skip the robot ID
            if item_id == self.robot_id:
                continue
            self.obstacles.append(item_id)
            if display:
                item_info = p.getBodyInfo(item_id)
                item_name = item_info[1].decode("utf-8")
                print(f"Item Name: {item_name}, ID: {item_id}")

        self.store_obstacles()
        return self.obstacles

    def execute(self, path):
        """
        Execute a given path using the OMPL interface and handle errors.

        Args:
            path: The path to be executed. This should be absolute path.
        """
        try:
            self.pb_ompl_interface.execute(path)
        except Exception as e:
            print(f"Error executing path: {e}")

    def compute_distance(self, end_effector_link_index):
        """
        Compute the distance between the end effector and the object.

        Args:
                end_effector_link_index: index of the end effector link.

        Returns:
                distance: distance between the end - effector and the object.
        """
        end_effector_pose = p.getLinkState(self.robot_id, end_effector_link_index)

        # Compute the distance between the end-effector and the object
        distance = np.linalg.norm(
            np.array(end_effector_pose[0]) - np.array(self.target_pos)
        )
        return distance

    def plan_grasp(self, start, goal):
        """
        Plan grasp from start to goal.
        This is a wrapper around OMPL grasp planning algorithm.

        Args:
                start: state to start planning from.
                goal: state to go to after planning has been completed.

        Returns:
            res: response from ompl interface.
                path: a list of robot state.
        """
        self.robot.set_state(start)
        res, path = self.pb_ompl_interface.plan(goal)
        return res, path

    def move_end_effector_to_goal_position(
        self, start, goal, end_effector_link_index
    ):  # TODO refactor
        """
        Move end effector to goal position in OMPL planned path.

        Args:
                start: effective position of end effector.
                goal: effective position of goal.
                end_effector_link_index: index of end effector.
        """
        grasp_successful = False

        trial = 0
        while not grasp_successful:
            trial += 1
            res, path = self.plan_grasp(start, goal)

            if res:
                self.pb_ompl_interface.execute(path, dynamics=True)
                # Pause briefly to simulate real-time
                time.sleep(0.1)
                grasp_successful = True
                print("After {} trials, finished.".format(trial))
                # step simulation in a loop.
                for _ in range(100):
                    p.stepSimulation()
                    time.sleep(1.0 / 100.0)

    def grasp_object(self, start, goal, end_effector_link_index):  # TODO refactor
        """
        Plans an object from start to goal and attaches it to the robot.

        Args:
                start: effective position of end effector.
                goal: effective position of goal.
                end_effector_link_index: index of end effector.
        """
        grasp_successful = False
        trial = 0
        while not grasp_successful:
            trial += 1
            res, path = self.plan_grasp(start, goal)

            # Execute the path and attach the object to the robot
            if res:
                self.pb_ompl_interface.execute(path)
                # Pause briefly to simulate real-time
                time.sleep(0.1)
                # Check if the robot is close to the object
                distance = self.compute_distance(end_effector_link_index)
                # This method grasses the robot if the distance is below threshold.
                if distance <= self.threshold:
                    grasp_successful = True
                    print("After {} trials, successfully grasped.".format(trial))
                    # Attach the object to the robot
                    cube_orn = p.getQuaternionFromEuler([0, math.pi, 0])
                    self.gripper_id = p.createConstraint(
                        self.robot_id,
                        end_effector_link_index,  # TODO refactor
                        self.target,
                        -1,
                        p.JOINT_FIXED,
                        [0, 0, 0],
                        [0, 0, 0.05],
                        [0, 0, 0],
                        childFrameOrientation=cube_orn,
                    )


class UR5_2F85:
    def __init__(self, client_id, robot_id):
        """
        Initializes the UR5 with Robotiq 2f85 gripper.

        Args:
                client_id: Client ID of pybullet.
                robot_id: The URDF object features a Robotiq 2f85 gripper.
        """
        self._cid = client_id
        self.Rob2f85 = Robotiq2F85(self._cid, robotUID=robot_id)
        self.robot_id = self.Rob2f85.getUID()
        self.end_effector_link_index = self.get_tool_center_point()
        self.build_joint_index_from_name_dict()

    def set_gripper_max_force(self, max_force):
        """
        Set gripper max force.

        Args:
                max_force: The max force of gripper motor control.
        """
        self.Rob2f85.setMaxForce(max_force)

    def init_joints(self, init_pose):
        """
        Initialize joints.

        Args:
                init_pose: a list of two representing the gripper closing.
        """
        r2f85 = init_pose
        self.Rob2f85.initJoints(r2f85)

    def close_gripper(self):
        """
        Close gripper and go to 80%.
        """
        self.set_gripper_goal(80, 80)

    def open_gripper(self):
        """
        Opens gripper.
        """
        self.set_gripper_goal(0, 0)

    def get_tool_center_point(self):
        """
        Get the index of tcp of gripper.


        Returns:
                end_effector_link_index: the index of the end effector joint.
        """
        end_effector_link_index = self.get_joint_index_from_name("tool_center_point")
        return end_effector_link_index

    def get_end_effector_info(self):
        """
        Get information about the end effector.

        Returns:
               end_effector_position: the end effector position in world coordinates.
           end_effector_orientation: the end effector orientation in world coordinates.
        """
        joint_info = p.getJointInfo(self.robot_id, self.end_effector_link_index)
        end_effector_name = joint_info[1].decode("utf-8")
        end_effector_info = p.getLinkState(
            bodyUniqueId=self.robot_id, linkIndex=self.end_effector_link_index
        )
        end_effector_position = end_effector_info[0]
        end_effector_orientation = end_effector_info[1]
        return end_effector_position, end_effector_orientation

    def build_joint_index_from_name_dict(self):
        """
        Build joint index from name dictionary.
        This is used to find the names of joints.
        """
        self.joints_names_dict = {}
        num_joints = p.getNumJoints(self.robot_id, self._cid)
        # Get the joint information from the robot.
        for i in range(num_joints):
            joint_info = p.getJointInfo(self.robot_id, i, self._cid)
            srt_joint_name = joint_info[1].decode("UTF-8")
            self.joints_names_dict[srt_joint_name] = joint_info[0]

    def get_joint_index_from_name(self, joint_name):
        """
        Get joint index from name.

        Args:
                joint_name: name of the joint to find.

        Returns:
                joint_id: index of the joint to find.
        """
        try:
            # build joint index from name dictionary
            if len(self.joints_names_dict) == 0:
                self.build_joint_index_from_name_dict()
        except AttributeError:
            self.build_joint_index_from_name_dict()

        try:
            joint_id = self.joints_names_dict.get(joint_name)
        except Exception as e:
            print(f"Cannot find a joint named: {e}")
        return joint_id

    def set_gripper_goal(self, left_finger_goal, right_finger_goal):
        """
        Set gripper goal in degrees.
        A percentage that specify how close each finger is (100 = Fully closed, 0 = Fully open).

        Args:
                left_finger_goal: the amount of left finger closing.
                right_finger_goal: the amount of right finger closing.
        """
        self.Rob2f85.setGoal(left_finger_goal, right_finger_goal)
