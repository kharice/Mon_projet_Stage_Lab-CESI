import math
import time
import threading
import numpy as np
import rclpy
from rclpy.executors import SingleThreadedExecutor
from geometry_msgs.msg import Twist
from flask import Flask, render_template, request, jsonify
from rdp import rdp
from sensor_msgs.msg import LaserScan

# Initialise ROS2 et cree le noeud qui publie les commandes de vitesse.
rclpy.init()
node = rclpy.create_node('flask_trajectory_node')
publisher = node.create_publisher(Twist, '/cmd_vel', 10)

# Application web Flask exposee pour piloter le robot.
app = Flask(__name__)

# Parametres de conversion pixel -> centimetres.
CELL_SIZE = 80
REAL_CELL_CM = 25
SCALE = REAL_CELL_CM / CELL_SIZE

# Etat global de l'execution de trajectoire.
stop_flag = False
last_commands = []
current_command_index = 0
robot_status = "idle"
scan_count = 0

# Trois variables LiDAR : devant, gauche, droite
min_front_distance = float('inf')
last_scan_left = float('inf')
last_scan_right = float('inf')

# Deux seuils de distance
DANGER_DISTANCE = 0.30   # 30cm -> STOP immediat
ALERT_DISTANCE  = 0.50   # 50cm -> ralentir et evaluer

obstacle_detected = False
obstacle_approaching = False

def scan_callback(msg):
    """Met a jour l'etat des obstacles a partir du LiDAR."""
    global obstacle_detected, obstacle_approaching
    global min_front_distance, last_scan_left, last_scan_right, scan_count

    num_readings = len(msg.ranges)
    if num_readings == 0:
        return

    # Supprime les valeurs LiDAR invalides/hors plage.
    def clean(ranges):
        return [r for r in ranges if 0.05 < r < 3.5 and not math.isnan(r) and not math.isinf(r)]

    # Zone devant : cone de 40 degres (+-20 degres)
    # On prend les 20 premiers et 20 derniers indices
    front_indices = list(range(0, 20)) + list(range(num_readings - 20, num_readings))
    front_ranges = clean([msg.ranges[i] for i in front_indices if i < num_readings])

    # Zone gauche : entre 45 et 135 degres
    left_start = num_readings // 8
    left_end = num_readings * 3 // 8
    left_ranges = clean(msg.ranges[left_start:left_end])

    # Zone droite : entre 225 et 315 degres
    right_start = num_readings * 5 // 8
    right_end = num_readings * 7 // 8
    right_ranges = clean(msg.ranges[right_start:right_end])

    min_front_distance = min(front_ranges) if front_ranges else float('inf')
    last_scan_left     = min(left_ranges)  if left_ranges  else float('inf')
    last_scan_right    = min(right_ranges) if right_ranges else float('inf')

    obstacle_detected    = min_front_distance < DANGER_DISTANCE
    obstacle_approaching = min_front_distance < ALERT_DISTANCE

    scan_count += 1
    if scan_count % 5 == 0:
        print(f"LiDAR -> Devant: {min_front_distance:.2f}m | Gauche: {last_scan_left:.2f}m | Droite: {last_scan_right:.2f}m | DANGER: {obstacle_detected} | ALERTE: {obstacle_approaching}")

# Souscription ROS2 au topic de scan LiDAR.
scan_subscriber = node.create_subscription(LaserScan, '/scan', scan_callback, 10)

# Executor dedie au spin ROS2 dans un thread separe du serveur Flask.
executor = SingleThreadedExecutor()
executor.add_node(node)

def spin_executor():
    """Boucle ROS2 qui traite les callbacks de capteurs."""
    executor.spin()

spin_thread = threading.Thread(target=spin_executor, daemon=True)
spin_thread.start()

def avoid_obstacle():
    """Tente un contournement simple en choisissant le cote le plus degage."""
    global obstacle_detected, obstacle_approaching, robot_status

    print(f"Evitement -> Gauche: {last_scan_left:.2f}m | Droite: {last_scan_right:.2f}m")

    # Verification si cul-de-sac
    if last_scan_left < DANGER_DISTANCE and last_scan_right < DANGER_DISTANCE:
        print("Cul-de-sac detecte ! Aucun echapatoire.")
        robot_status = "blocked"
        publisher.publish(Twist())
        return False

    # Choix du cote le plus degage
    turn_direction = 0.5 if last_scan_left > last_scan_right else -0.5
    side = 'gauche' if turn_direction > 0 else 'droite'
    print(f"Contournement par la {side}")

    max_attempts = 3
    attempts = 0

    while attempts < max_attempts:
        if stop_flag:
            break

        # Etape 1 : Rotation vers le cote libre
        twist = Twist()
        twist.angular.z = turn_direction
        publisher.publish(twist)
        time.sleep(1.2)
        publisher.publish(Twist())
        time.sleep(0.2)

        # Etape 2 : Avancer lentement jusqu'a depasser l'obstacle
        elapsed = 0
        while (obstacle_detected or obstacle_approaching) and elapsed < 3.0:
            if stop_flag:
                break
            twist = Twist()
            twist.linear.x = 0.06
            publisher.publish(twist)
            time.sleep(0.1)
            elapsed += 0.1
            print(f"Contournement en cours: devant={min_front_distance:.2f}m")

        publisher.publish(Twist())
        time.sleep(0.3)

        # Etape 3 : Verifier si la voie est libre
        if not obstacle_detected and not obstacle_approaching:
            # Retour dans l'axe initial
            twist = Twist()
            twist.angular.z = -turn_direction
            publisher.publish(twist)
            time.sleep(1.2)
            publisher.publish(Twist())
            time.sleep(0.2)
            robot_status = "moving"
            print("Obstacle contourne avec succes !")
            return True

        attempts += 1
        turn_direction = -turn_direction
        print(f"Tentative {attempts}/{max_attempts} echouee, essai de l'autre cote...")

    robot_status = "blocked"
    publisher.publish(Twist())
    print("Robot bloque ! Intervention humaine necessaire.")
    return False

def convert_to_commands(real_points):
    """Transforme une liste de points en rotations + distances executables."""
    commands = []
    current_angle = 90.0

    for i in range(1, len(real_points)):
        prev = real_points[i-1]
        curr = real_points[i]

        dx = -(curr['x'] - prev['x'])
        dy = curr['y'] - prev['y']
        distance = math.sqrt(dx**2 + dy**2)

        if distance < 0.5:
            continue

        target_angle = math.degrees(math.atan2(dy, dx))
        rotation = target_angle - current_angle

        if rotation > 180:
            rotation -= 360
        elif rotation < -180:
            rotation += 360

        commands.append({
            'rotation': round(rotation, 2),
            'distance': round(distance, 2)
        })
        current_angle = target_angle

    return commands

def execute_commands(commands, start_index=0):
    """Execute les commandes de trajectoire en surveillant les obstacles."""
    global stop_flag, current_command_index, robot_status
    LINEAR_SPEED  = 0.15
    ANGULAR_SPEED = 0.20

    robot_status = "moving"

    for i, cmd in enumerate(commands[start_index:], start=start_index):
        current_command_index = i

        if stop_flag or robot_status == "blocked":
            break

        # Conversion degres -> radians, cm -> metres pour commander ROS.
        rotation_rad = math.radians(cmd['rotation'])
        distance_m   = cmd['distance'] / 100.0

        # Rotation
        if abs(rotation_rad) > 0.01:
            rotation_time = abs(rotation_rad) / ANGULAR_SPEED
            twist = Twist()
            twist.angular.z = ANGULAR_SPEED if rotation_rad > 0 else -ANGULAR_SPEED
            publisher.publish(twist)

            elapsed = 0
            while elapsed < rotation_time:
                if stop_flag:
                    break
                time.sleep(0.1)
                elapsed += 0.1

            publisher.publish(Twist())
            time.sleep(0.2)

        if stop_flag or robot_status == "blocked":
            break

        # Avancement avec detection anticipee
        if distance_m > 0.001:
            move_time = distance_m / LINEAR_SPEED
            elapsed   = 0

            while elapsed < move_time:
                if stop_flag:
                    break

                if obstacle_detected:
                    # DANGER : arret immediat et evitement
                    publisher.publish(Twist())
                    time.sleep(0.2)
                    success = avoid_obstacle()
                    if not success:
                        return

                elif obstacle_approaching:
                    # ALERTE : ralentir
                    twist = Twist()
                    twist.linear.x = LINEAR_SPEED * 0.5
                    publisher.publish(twist)

                else:
                    # LIBRE : vitesse normale
                    twist = Twist()
                    twist.linear.x = LINEAR_SPEED
                    publisher.publish(twist)

                time.sleep(0.1)
                elapsed += 0.1

            publisher.publish(Twist())
            time.sleep(0.2)

    publisher.publish(Twist())
    # Retour etat final: idle quand le cycle se termine (normalement ou stop).
    if robot_status != "blocked":
        if not stop_flag:
            robot_status = "idle"
        else:
            robot_status = "idle"

@app.route('/')
def index():
    """Page d'accueil."""
    return render_template('index.html')

@app.route('/trajectory')
def trajectory():
    """Page de dessin de trajectoire."""
    return render_template('trajectory.html')

@app.route('/robot_status', methods=['GET'])
def get_robot_status():
    """Expose l'etat courant du robot pour le frontend."""
    return jsonify({'status': robot_status})

@app.route('/send_trajectory', methods=['POST'])
def send_trajectory():
    """Recoit les points du canvas, les simplifie, puis lance l'execution."""
    global stop_flag, last_commands, robot_status
    data   = request.get_json()
    points = data['points']

    # Recalage du repere canvas vers un repere centre robot.
    real_points = []
    for point in points:
        real_x = (point['x'] - 240) * SCALE
        real_y = (point['y'] - 160) * SCALE
        real_points.append({'x': real_x, 'y': real_y})

    # Simplification de trajectoire pour reduire le nombre de commandes.
    points_array = [[p['x'], p['y']] for p in real_points]
    simplified   = rdp(points_array, epsilon=2.0)
    real_points  = [{'x': p[0], 'y': p[1]} for p in simplified]

    commands = convert_to_commands(real_points)

    stop_flag    = False
    last_commands = commands
    robot_status  = "moving"

    print("Commandes calculees :")
    for cmd in commands:
        print(f"  Rotation: {cmd['rotation']}° | Distance: {cmd['distance']} cm")

    # Execution dans un thread pour ne pas bloquer la requete HTTP.
    thread = threading.Thread(target=execute_commands, args=(commands,))
    thread.start()

    return jsonify({'status': 'ok', 'commands': commands})

@app.route('/stop_robot', methods=['POST'])
def stop_robot():
    """Arret immediat du robot et remise a l'etat idle."""
    global stop_flag, robot_status
    stop_flag    = True
    robot_status = "idle"
    publisher.publish(Twist())
    print("Robot arrete !")
    return jsonify({'status': 'ok'})

@app.route('/resume_robot', methods=['POST'])
def resume_robot():
    """Reprend la trajectoire a partir de la derniere commande active."""
    global stop_flag, last_commands, current_command_index, robot_status
    stop_flag    = False
    robot_status = "moving"
    if last_commands:
        thread = threading.Thread(
            target=execute_commands,
            args=(last_commands, current_command_index)
        )
        thread.start()
        return jsonify({'status': 'ok'})
    return jsonify({'status': 'error', 'message': 'Aucune trajectoire a reprendre'})

if __name__ == '__main__':
    # Lancement du serveur web sur toutes les interfaces reseau.
    app.run(host='0.0.0.0', port=5000, debug=False)