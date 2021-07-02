"""
2D Truss Calculator
Version 1.4
"""

import math, keyword, os, warnings, subprocess, sys, re
from functools import total_ordering                    # builtin modules

# Automatically install missing modules, least likely to be already installed first
try:
    import sigfig                                       # required for rounding values nicely
    from scipy.sparse import linalg, csr_matrix         # used for faster solving
    from matplotlib import pyplot as plt                # used to display graphical output
    import numpy as np                                  # used to do matrix operations

except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "sigfig", "scipy", "numpy", "matplotlib"])
    print(' \t ~~ All dependencies succesfully installed. ~~ \n\n')

finally:
    import numpy as np
    from matplotlib import pyplot as plt
    from scipy.sparse import linalg, csr_matrix
    import sigfig


# Allow creation of multiple trusses
class ClassIter(type):
    """
    A helper metaclass to support iteration over class instances. For reference see
    https://codereview.stackexchange.com/questions/126100/recording-all-instances-of-a-class-python
    https://stackoverflow.com/questions/28676399/iteration-over-class-instances-using-iter
    """
    def __iter__(cls):
        return iter(cls._ClassRegistry)

    def __len__(cls):
        return len(cls._ClassRegistry)


# MAIN CLASS FOR TRUSSES

@total_ordering
class Truss(metaclass=ClassIter):
    """
    A class containing the truss to be worked with.
    """
    _ClassRegistry = []

    def __init__(self, bar_params: dict = None, units='kN, mm'):
        """
        Initialise a truss by setting the units system to be used
        and the default properties (thickness, modulus etc) which
        bars will have when added.
        """
        self._ClassRegistry.append(self)  # add the new truss object to the list of trusses
        if bar_params is None:  # set the units that the calculations should be done in
            if units == 'N, m':
                self.default_params = {"b": 0.016, "t": 0.004, "D": 0.020,
                                       "E": 2.1e11}  # some default values. symbols defined in databook
            elif units == 'kN, mm':
                self.default_params = {"b": 1.6, "t": 4, "D": 20, "E": 210}  # same values as above but in other units
            else:
                raise ValueError('Units must be either "N, m" or "kN, mm".')
        else:
            self.default_params = bar_params
        
        self.units = units

    # PARTS OF THE TRUSS (INNER CLASSES)

    class Joint(metaclass=ClassIter):
        """
          Joints define the locations where other objects can go.
          Bars go between two joints.
          Each joint can have loads and supports applied.
          """
        _ClassRegistry = []

        def __init__(self, truss: object, name: str, x: float, y: float):

            self.name = name

            if not self.name in (i.name for i in self._ClassRegistry):
                            self._ClassRegistry.append(self)
    
            self.truss = truss
            self.x = x
            self.y = y
            self.loads = {}

    class Bar(metaclass=ClassIter):
        """
          Bars go between the first_joint and the second_joint.
          Each bar can have different thickness, strength etc in my_params.
          """
        _ClassRegistry = []

        def __init__(self, truss: object, name: str, first_joint: object, second_joint: object,
                     my_params: dict = None):
            """
            Initialise a bar with a given name, which joints it should connect between, and
            its physical properties.
            """

            self.name = name  # The user-defined name

            if not self.name in (i.name for i in self._ClassRegistry):
                self._ClassRegistry.append(self)
            
            self.truss = truss  # the class which this bar belongs to
            self.first_joint, self.first_joint_name = first_joint, first_joint.name         # the object and name of the first and
            self.second_joint, self.second_joint_name = second_joint, second_joint.name     # secondjoint this bar is connected
            self.params = truss.default_params if my_params is None else my_params  # take the truss's default if bar not given any

            # physical and geometric properties of the bar, as defined in databook
            [setattr(self, attr, self.params[attr]) for attr in ["b", "t", "D", "E", "strength_max"]]
            self.length = math.sqrt((self.first_joint.x - self.second_joint.x)**2 + (self.first_joint.y - self.second_joint.y)**2)
            self.section_area = (self.b ** 2 - (self.b - self.t) ** 2) * 1.03
            self.effective_area = (1.5 * self.b - self.D) * 0.9 * self.t
            self.buckling_ratio = self.length / self.b

        def get_direction(self, origin_joint: object):
            """
            Calculates the (polar, in radians) angle this bar makes with the horizontal, 
            with the origin taken as the origin_joint. 0 = horizontal right, +pi/2 = vertical up, 
            -pi/2 = vertical down, pi = horizontal left, etc. (-pi < angle <= pi).
            """
            connected_joints = self.truss.get_all_joints_connected_to_bar(self)
            if origin_joint == connected_joints[0]:
                angle = math.atan2(connected_joints[1].y - origin_joint.y,
                                    connected_joints[1].x - origin_joint.x)
            elif origin_joint == connected_joints[1]:
                angle = math.atan2(connected_joints[0].y - origin_joint.y, 
                                    connected_joints[0].x - origin_joint.x)
            else:
                raise LookupError(f'The bar "{self.name}" appears to be not attached to a joint at both its ends.')

            return angle

    class Load(metaclass=ClassIter):
        """
          Loads can be applied at any joint.
          Their components are specified as (x_comp, y_comp)
          aligned with the coordinate system used to define the joints.
          """
        _ClassRegistry = []

        def __init__(self, name: str, joint: object, x_comp: float = 0.0, y_comp: float = 0.0):
            """
            Initialise a load with a name, a joint to be applied at, and a force value in terms
            of x and y components (as defined by the coordinate and unit systems).
            """

            self.name = name

            if not self.name in (i.name for i in self._ClassRegistry):
                self._ClassRegistry.append(self)

            self.joint = joint
            self.x, self.y = x_comp, y_comp
            self.magnitude = math.sqrt(self.x ** 2 + self.y ** 2)  # magnitude of the force
            self.direction = math.atan2(self.y, self.x)  # direction of the force clockwise from the positive x-axis
            joint.loads[self.name] = (self.x, self.y)  # add this load's components to the joint's dict attribute

    class Support(metaclass=ClassIter):
        """
        Supports are points from which external reaction forces can be applied.
        """
        _ClassRegistry = []

        def __init__(self, truss: object, name: str, joint: object, support_type: str = 'encastre',
                     roller_normal_vector: tuple = (1, 0)):
            """
            Initialise a support with a name, a joint object to convert to a support, the type of support
            and a direction if a roller joint is chosen.
            """

            self.name = name

            if not self.name in (i.name for i in self._ClassRegistry):
                self._ClassRegistry.append(self)
            
            self.joint = joint
            self.type = support_type
            self.dir = roller_normal_vector
            if self.type in ('encastre', 'pin'):
                joint.loads['Reaction @ {}'.format(self.name)] = (None, None)
                # Independent unknowns: fill in later
            elif self.type == 'roller':
                joint.loads['Reaction @ {}'.format(self.name)] = (None * self.dir[0], None * self.dir[1])
                # Dependent unknowns: fill in later
            else:
                raise ValueError('Support type must be "encastre", "roller" or "pin".')


    # TRUSS RESULTS CLASS

    class Result:
        """
        Allows the results to be analysed and manipulated.
        """

        def __init__(self, truss, sig_figs=None, solution_method="NUMPY.STANDARD", delete_truss_after=False):
            self.truss = truss
            self.results = truss.calculate(solution_method=solution_method)
            self.tensions, self.reactions, self.stresses, self.strains, self.buckling_ratios = {}, {}, {}, {}, {}
            self.sig_figs = sig_figs
            warnings.filterwarnings('ignore')
            self.get_data(truss)
            self.round_data()
            if delete_truss_after:
                truss._delete_truss()

        def __repr__(self):
            repr_str  = f'\n Axial forces are: '\
                        f'(positive = tension; negative = compression) \n \t {str(self.tensions)}'
            repr_str += f'\n Axial stresses are: \n \t {str(self.stresses)}'
            repr_str += f'\n Reaction forces are (horizontal, vertical) components (signs '\
                        f'consistent with coordinate system): \n \t {str(self.reactions)}'
            repr_str += f'\n Buckling ratios are: \n \t {str(self.buckling_ratios)}'
            repr_str += f'\n Strains are: \n \t {str(self.strains)}'
            repr_str += f'\n\n Units are {self.truss.units.split(",")[0]}, values '\
                        f'{f"not rounded" if self.sig_figs is None else f"rounded to {self.sig_figs} s.f."}'
            return repr_str

        def round_data(self):
            """
            Replaces the calculated data with rounded values, to precision given by Truss.Result.sig_figs.
            """
            for item in list(self.tensions.keys()):
                try:
                    self.tensions[item] = sigfig.round(self.tensions[item], self.sig_figs)
                    self.stresses[item] = sigfig.round(self.stresses[item], self.sig_figs)
                    self.strains[item] = sigfig.round(self.strains[item], self.sig_figs)
                    self.buckling_ratios[item] = sigfig.round(self.buckling_ratios[item], self.sig_figs)
                except KeyError:
                    continue

            for item in list(self.reactions.keys()):
                try:
                    self.reactions[item] = (sigfig.round(self.reactions[item][0], self.sig_figs),
                                            sigfig.round(self.reactions[item][1], self.sig_figs))
                except KeyError:
                    continue

        def get_data(self, truss):
            """
            Calculate tensions, stresses, strains, reaction forces and buckling ratios
            from the calculate() function.
            """
            for item in self.results:
                if isinstance(self.results[item], float):
                    if abs(self.results[item]) < 1e-10:
                        self.tensions.update({item: 0})
                    else:
                        self.tensions.update({item: self.results[item]})
                    self.stresses.update({item: self.tensions[item] / truss.get_bar_by_name(item).effective_area})
                    self.strains.update({item: self.stresses[item] / truss.get_bar_by_name(item).E})
                    if self.results[item] < 0:
                        self.buckling_ratios.update({item: truss.get_bar_by_name(item).buckling_ratio})
                elif isinstance(self.results[item], tuple):
                    self.results[item] = tuple(map(lambda x: round(x, self.sig_figs), self.results[item]))
                    self.reactions.update({item: self.results[item]})
                else:
                    raise TypeError('An internal error occured.')

    # TRUSS METHODS

    def calculate(self, solution_method="SCIPY"):

        """
        The main part of the program. Calculates the forces in the truss's bars and supports
        in order to maintain force equilibrium with the given loads. Outputs as a dictionary in the form
        {bar_name: axial_force_value} + {support_name: (reaction_force_value_x, reaction_force_value_y)}
        """

        # Get a list of the distinct joint names, number of equations to form = 2 * number of joints
        joint_names = self.get_all_joints(str_names_only=True)

        # List of dictionaries for unknowns, given default zero values
        wanted_vars = []
        for bar in self.get_all_bars():
            wanted_vars.append('Tension in ' + bar.name)
        for support in self.get_all_supports():
            wanted_vars.append('Horizontal reaction at ' + support.joint.name)
            wanted_vars.append('Vertical reaction at ' + support.joint.name)

        all_directions = {}
        for joint in self.get_all_joints():
            # Reset the directions dictionary for this joint
            directions = {}
            connected_bars = self.get_all_bars_connected_to_joint(joint)

            # Get the anticlockwise (polar) angle of each connected joint relative to this joint which have bars
            for bar in connected_bars:
                angle = bar.get_direction(joint)
                directions['Tension in ' + bar.name] = angle

            # If there are reactions at this joint, store their directions too
            if any([s.joint.name == joint.name for s in self.get_all_supports()]):
                directions['Horizontal reaction at ' + joint.name] = 0
                directions['Vertical reaction at ' + joint.name] = math.pi / 2

            # If there are external loads at this joint, store their directions too
            for load in self.get_all_loads_at_joint(joint):
                directions['Horizontal component of {} at {}'.format(load.name, joint.name)] = 0
                directions['Vertical component of {} at {}'.format(load.name, joint.name)] = math.pi / 2

            all_directions[joint.name] = directions

        # Store the coefficients of the unknowns in each equation
        coefficients = []
        for joint_name in joint_names:
            current_line = [round(math.cos(all_directions[joint_name].get(var, math.pi/2)), 10) for var in wanted_vars]
            coefficients.append(current_line)
            current_line = [round(math.sin(all_directions[joint_name].get(var, 0)), 10) for var in wanted_vars]
            coefficients.append(current_line)

        # Store the constants of each equation, negative since they are on the other side of the system of equations
        constants = []
        for joint_name in joint_names:
            try:
                constants.append([-1 * sum(load.x) for load in 
                                  self.get_all_loads_at_joint_by_name(joint_name)])
                constants.append([-1 * sum(load.y) for load in
                                  self.get_all_loads_at_joint_by_name(joint_name)])
            except TypeError:
                constants.append([-1 * load.x for load in
                                  self.get_all_loads_at_joint_by_name(joint_name)])
                constants.append([-1 * load.y for load in
                                  self.get_all_loads_at_joint_by_name(joint_name)])

        # Sanitise load data
        for i in range(len(constants)):
            if constants[i] == [] or constants[i] == [None]:
                constants[i] = [0]
        
        # Solve the system - both coefficient and constant matrices are sparse so ideally the SCIPY method is faster.
        _method_parse = solution_method.split('.')
        if _method_parse[0] == "NUMPY":
            m, b = np.matrix(coefficients), np.matrix(constants)
            if _method_parse[1] == "STANDARD":
                x = np.linalg.inv(m) * b
            elif _method_parse[1] == "SOLVE":
                x = np.linalg.solve(m, b)
        elif _method_parse[0] == "SCIPY":
            x = linalg.spsolve(csr_matrix(coefficients), csr_matrix(constants))

        # Match values back to variable names
        output_dict = {}
        for i, bar in enumerate(self.get_all_bars()):
            output_dict[bar.name] = float(x[i])
        else:
            _i = i
        for support in self.get_all_supports():
            output_dict[support.name] = (float(x[_i]), float(x[_i + 1]))
            _i += 2

        # For whatever reason, sometimes the reaction forces are wrong. Correct them here by resolving at the supports
        for support in self.get_all_supports():
            reaction_corrected = [0, 0]
            for bar in self.get_all_bars_connected_to_joint(support.joint):
                angle = bar.get_direction(support.joint)
                reaction_corrected[0] -= output_dict[bar.name] * math.cos(angle)
                reaction_corrected[1] -= output_dict[bar.name] * math.sin(angle)

            output_dict[support.name] = (reaction_corrected[0], reaction_corrected[1])
        
        # Return the values in dict form
        print(wanted_vars)
        print(directions)
        return output_dict

    def is_statically_determinate(self):
        """
        Does a simple arithmetic check to estimate if the truss
        is statically determinate (b + F = 2j). Also stores attributes for later quick use.
        """
        self.b = len(self.get_all_bars(str_names_only=True))
        self.F = sum([2 if support.type in ('encastre', 'pin') else 1 for support in Truss.Support])
        self.j = len(self.get_all_joints(str_names_only=True))
        return self.b + self.F == 2 * self.j

    def _delete_truss(self):
        """
        Delete the truss and clear the _ClassRegistry when the calculation for a truss 
        is done. Required to prevent the _ClassRegistry adding duplicate objects.
        """
        _ClassRegistry = []
        self.Joint._ClassRegistry, self.Bar._ClassRegistry, self.Support._ClassRegistry, self.Load._ClassRegistry = [], [], [], []

    '''
    Allow ordering of the trusses by their position in the _ClassRegistry
    which represents the order they were created in. Used by @functools.total_ordering.
    '''
    def __lt__(self, other):
        return self._ClassRegistry.index(self) < self._ClassRegistry.index(other)

    def __ge__(self, other):
        return self._ClassRegistry.index(self) >= self._ClassRegistry.index(other)
    
    
    """
    Returns all objects of a given type by their name or object reference.
    """

    @staticmethod
    def get_all_bars(str_names_only: bool = False):
        """
        Returns a list of bar objects or strings in this truss.
        """
        if not str_names_only:
            return [bar for bar in Truss.Bar]
        else:
            return {bar.name for bar in Truss.Bar}

    @staticmethod
    def get_all_joints(str_names_only: bool = False):
        """
        Returns a list of all joint objects or strings in this truss.
        """
        if not str_names_only:
            return [joint for joint in Truss.Joint]
        else:
            return {joint.name for joint in Truss.Joint}

    @staticmethod
    def get_all_bars_connected_to_joint(joint: object, str_names_only: bool = False):
        """
        Returns a list of bar objects or names which are connected to a given joint object.
        """
        if not str_names_only:
            return [bar for bar in Truss.Bar if joint.name in {bar.first_joint.name, bar.second_joint.name}]
        else:
            return {bar.name for bar in Truss.Bar if joint.name in {bar.first_joint.name, bar.second_joint.name}}

    @staticmethod
    def get_all_joints_connected_to_bar(bar: object, str_names_only: bool = False):
        """
        Returns a list of joint objects or names which are connected to a given bar object.
        """
        if not str_names_only:
            return (bar.first_joint, bar.second_joint)
        else:
            return (bar.first_joint.name, bar.second_joint.name)

    @staticmethod
    def get_all_loads():
        """
        Returns a list of load objects in the truss.
        """
        return [load for load in Truss.Load]

    @staticmethod
    def get_all_loads_at_joint(joint: object):
        """
        Returns a list of load objects which are applied at a given joint object.
        """
        return [load for load in Truss.Load if load.joint == joint]

    @staticmethod
    def get_all_loads_at_joint_by_name(joint_name: str):
        """
        Returns a list of load objects which are applied at a given joint name.
        """
        return [load for load in Truss.Load if load.joint.name == joint_name]

    @staticmethod
    def get_all_supports():
        """
        Returns a list of support objects in the truss.
        """
        return [support for support in Truss.Support]

    @staticmethod
    def get_bar_by_name(bar_name: str):
        """
        Returns the corresponding bar object given a bar name.
        """
        for bar in Truss.Bar:
            if bar.name == bar_name:
                return bar


# TRUSS INNER CLASSES END HERE, MAIN RESULTS FUNCTIONS START HERE

def plot_diagram(truss: object, results: object, show_reactions=False, delete_truss_after=True):
    """
     Create a matplotlib output image showing the truss geometry,
     annotated with arrows and labels.
     """

    # Find a suitable length-scale to make the annotations look nicer - 10% of the average length of the bars
    arrow_sizes = [x.length for x in truss.get_all_bars()]
    arrow_sizes = sum(arrow_sizes) / len(arrow_sizes) * 0.1

    # Plot all joints
    plt.plot([joint.x for joint in truss.get_all_joints()], [joint.y for joint in truss.get_all_joints()], 'o')

    # Plot all bars and label their axial forces in the legend
    for bar in truss.get_all_bars():

        plt.plot([bar.first_joint.x, bar.second_joint.x], [bar.first_joint.y, bar.second_joint.y],
                 label='{}'.format(bar.name + ': ' + str(results.tensions[bar.name]) + ' ' + truss.units.split(',')[0]),
                 zorder=0)

        # If the bar is nearly vertical, label its name to its right, otherwise label it above
        if 80 * (math.pi / 180) <= abs(math.atan2(bar.second_joint.y - bar.first_joint.y,
                                                  bar.second_joint.x - bar.first_joint.x)) <= 100 * (math.pi / 180):
            plt.text(sum([bar.first_joint.x, bar.second_joint.x]) / 2 + arrow_sizes / 3,
                     sum([bar.first_joint.y, bar.second_joint.y]) / 2, bar.name)
        else:
            plt.text(sum([bar.first_joint.x, bar.second_joint.x]) / 2,
                     sum([bar.first_joint.y, bar.second_joint.y]) / 2 + arrow_sizes / 3, bar.name)

    # Plot all support points with their reactions as arrows
    for support in truss.get_all_supports():

        plt.plot(support.joint.x, support.joint.y, '*', color='red',
                 label=support.name + ': ' + str(results.reactions[support.name]) + ' ' + truss.units.split(',')[0])

    for support in truss.get_all_supports():
        if show_reactions:
            direction_of_reaction = math.atan2(results.reactions[support.name][1], results.reactions[support.name][0])
            plt.arrow(support.joint.x, support.joint.y, arrow_sizes, 0,
                      head_width=arrow_sizes / 5, head_length=arrow_sizes / 4)
            plt.arrow(support.joint.x, support.joint.y, 0, arrow_sizes,
                      head_width=arrow_sizes / 5, head_length=arrow_sizes / 4)

        plt.text(support.joint.x + arrow_sizes / 4, support.joint.y + arrow_sizes / 4, support.name,
                 label=f'{support.name}: {str(results.reactions[support.name])} {truss.units.split(",")[0]}')

    # Plot all loads
    for load in truss.get_all_loads():
        direction_of_load = math.atan2(load.y, load.x)
        plt.arrow(load.joint.x, load.joint.y, arrow_sizes * math.cos(direction_of_load),
                  arrow_sizes * math.sin(direction_of_load),
                  head_width=arrow_sizes / 5, head_length=arrow_sizes / 4)
        plt.text(sum([load.joint.x, load.joint.x + arrow_sizes * math.cos(direction_of_load)]) / 2 + arrow_sizes / 3,
                 sum([load.joint.y + load.joint.y, arrow_sizes * math.sin(direction_of_load)]) / 2,
                 f'{load.name}: ({str(load.x)}, {str(load.y)}) {truss.units.split(",")[0]}')

    # Delete the truss registry to avoid issues if building another truss
    if delete_truss_after:
        truss._delete_truss()

    # Graphical improvements
    plt.legend(loc='upper right')
    plt.autoscale()
    plt.axis('equal')
    plt.xlabel(f'$x$-position / {truss.units.split(",")[1]}')
    plt.ylabel(f'$y$-position / {truss.units.split(",")[1]}')
    plt_set_fullscreen(plt)
    plt.show()


# HELPER FUNCTIONS BEGIN HERE

def validate_var_name(var_name: str, allow_existing_vars=True):
    """
    Checks if a var_name, which is used internally to instantiate the
    subclass objects (Joint, Bars, Load, Support). They are set using
    globals() where the key is var_name and the object reference is
    the value.
    """

    if var_name in globals() and not allow_existing_vars:
        raise NameError(f'A global variable {var_name} (with the value {globals()[var_name]}) is already in use.'
                        f'It cannot be used in the truss.')
    elif not var_name.isidentifier() or keyword.iskeyword(var_name):
        raise SyntaxError(f'{var_name} is not a valid variable name.'
                          f'It can only contain alphanumerics and underscores.')
    else:
        return True


def plt_set_fullscreen(plt):
    """
    Automatically set the matplotlib output to fullscreen.
    """

    backend = str(plt.get_backend())
    mgr = plt.get_current_fig_manager()
    if backend == 'TkAgg':
        if os.name == 'nt':
            mgr.window.state('zoomed')
        else:
            mgr.resize(*mgr.window.maxsize())
    elif backend == 'wxAgg':
        mgr.frame.Maximize(True)
    elif backend in ['Qt4Agg', 'Qt5Agg']:
        mgr.window.showMaximized()
    else:
        raise EnvironmentError(f'The backend in use, {backend}, is not supported in fullscreen mode.')



# OBJECT BUILDERS BEGIN HERE

'''
Allows trusses to be constructed with user-defined names instead of fixed variable names.
Objects are still stored internally with names given by var_name
but displayed to the user as joint_name, bar_name, load_name, support_name.

This is done by directly accessing the globals() dictionary
and adding {var_name : some_object_reference} to it.
'''


def create_joint(truss: object, var_name: str, joint_name: str, x: float, y: float, print_info=False):
    """
    Create an instance of a joint in a truss, with a user defined name joint_name,
    stored internally as var_name, at position (x, y).
    """

    if validate_var_name(var_name):
        globals()[var_name] = truss.Joint(truss, joint_name, x, y)

    if print_info:
        print(f'The joint with name "{globals()[var_name].name}", internally stored as "{var_name}", '
        f'has been assigned the location ({globals()[var_name].x}, {globals()[var_name].y})')


def create_bar(truss: object, var_name: str, bar_name: str, first_joint_var_name: str,
               second_joint_var_name: str, params: dict = None, print_info=False):
    """
    Create an instance of a bar in a truss, with a user defined name bar_name,
    stored internally as var_name, between two joints with string names, with bar_params.
    """

    if validate_var_name(var_name):
        globals()[var_name] = truss.Bar(truss, bar_name, globals()[first_joint_var_name],
                                        globals()[second_joint_var_name], params)

    if print_info:
        print(f'The bar with name "{globals()[var_name].name}", internally stored as "{var_name}", '
        f'has been placed between joints named ({globals()[first_joint_var_name].name}, '
        f'{globals()[second_joint_var_name].name}), internally stored as '
        f'({first_joint_var_name}, {second_joint_var_name}).')


def create_load(truss: object, var_name: str, load_name: str,
                joint_var_name: str, x: float, y: float, print_info=False):
    """
    Create an instance of a load in a truss, with a user defined name load_name,
    stored internally as var_name, at joint string joint_var_name, with components (x, y).
    """

    if validate_var_name(var_name):
        globals()[var_name] = truss.Load(load_name, globals()[joint_var_name], x, y)

    if print_info:
        print(f'The load with name "{globals()[var_name].name}", internally stored as "{var_name}", '
        f'has been applied at joint named {globals()[joint_var_name].name}, internally stored as "{joint_var_name}", '
        f'with components ({x}, {y}).')


def create_support(truss: object, var_name: str, support_name: str, joint_var_name: str,
                   support_type: str, direction: tuple = (1, 0), print_info=False):
    """
    Create an instance of a support in a truss, with a user defined name support_name,
    stored internally as var_name, at joint variable name string joint_var_name.
    """
        
    if validate_var_name(var_name):
        globals()[var_name] = truss.Support(truss, support_name,
                                            globals()[joint_var_name], support_type, direction)

    if print_info:
        print(f'The support with name "{globals()[var_name].name}", internally stored as "{var_name}", '
        f'has been applied at joint named {globals()[joint_var_name].name}, internally stored as "{joint_var_name}", '
        f'with type "{support_type}" in direction {direction}.')


"""---------------------------------------------------------------------------------------"""
    #####################################################
    #           PROGRAM EXECUTION STARTS HERE           #
    #####################################################
"""---------------------------------------------------------------------------------------"""


#  Fix issue with warning appearing when run from .exe
if os.path.basename(__file__).endswith('.exe'):
    warnings.filterwarnings("ignore", "(?s).*MATPLOTLIBDATA.*",
                                    category=UserWarning)  # deprecation warning inherits from UserWarning

if __name__ == "__main__":

    # -- An example truss --

    # Define some example bar parameters, four choices of bar
    weak = {"b": 12.5, "t": 0.7, "D": 5, "E": 210, "strength_max": 0.216}
    medium_1 = {"b": 16, "t": 0.9, "D": 5, "E": 210, "strength_max": 0.216}
    medium_2 = {"b": 16, "t": 1.1, "D": 5, "E": 210, "strength_max": 0.216}
    strong = {"b": 19, "t": 1.1, "D": 5, "E": 210, "strength_max": 0.216}

    # Define some custom bar parameters and initialise the truss
    custom_params = weak
    myTruss = Truss(custom_params, 'kN, mm')

    # Step 1. Create the joints
    create_joint(myTruss, 'joint_a', 'Joint A', 0, 0)
    create_joint(myTruss, 'joint_b', 'Joint B', 290, -90)
    create_joint(myTruss, 'joint_c', 'Joint C', 815, 127.5)
    create_joint(myTruss, 'joint_d', 'Joint D', 290, 345)
    create_joint(myTruss, 'joint_e', 'Joint E', 0, 255)
    create_joint(myTruss, 'joint_f', 'Joint F', 220.836, 127.5)

    # Step 2. Create the bars
    create_bar(myTruss, 'bar_1', 'Bar AB', 'joint_a', 'joint_b', medium_2)
    create_bar(myTruss, 'bar_2', 'Bar BC', 'joint_b', 'joint_c', strong)
    create_bar(myTruss, 'bar_3', 'Bar CD', 'joint_c', 'joint_d', medium_1)
    create_bar(myTruss, 'bar_4', 'Bar DE', 'joint_d', 'joint_e', medium_1)
    create_bar(myTruss, 'bar_5', 'Bar EF', 'joint_e', 'joint_f', medium_1)
    create_bar(myTruss, 'bar_6', 'Bar AF', 'joint_f', 'joint_a', medium_2)
    create_bar(myTruss, 'bar_7', 'Bar DF', 'joint_f', 'joint_d', medium_1)
    create_bar(myTruss, 'bar_8', 'Bar BF', 'joint_f', 'joint_b', weak)

    # Step 3. Create the loads
    create_load(myTruss, 'load_c', 'W', 'joint_c', 0, -0.675 * 1)

    # Step 4. Create the supports
    create_support(myTruss, 'support_a', 'Support A', 'joint_a', 'encastre')
    create_support(myTruss, 'support_e', 'Support E', 'joint_e', 'encastre')

    try:  # Get the results of the truss calculation and display graphic
        my_results = myTruss.Result(myTruss, sig_figs=3, solution_method="NUMPY.STANDARD")
        print(my_results)
    except np.linalg.LinAlgError:  # The truss was badly made, so could not be solved
        valid = myTruss.is_statically_determinate()
        if not valid:
            raise ArithmeticError('''The truss is not statically determinate. 
              It cannot be solved. \nBars: {}\nReactions: {}\nJoints: {}'''.format(
                myTruss.b, myTruss.F, myTruss.j))
        else:  # Some other issue occured. May require attention to the code.
            raise Exception("Something else went wrong. Couldn't identify the problem.")

    plot_diagram(myTruss, my_results, show_reactions=True)