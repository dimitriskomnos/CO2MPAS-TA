#-*- coding: utf-8 -*-
#
# Copyright 2015 European Commission (JRC);
# Licensed under the EUPL (the 'Licence');
# You may not use this work except in compliance with the Licence.
# You may obtain a copy of the Licence at: http://ec.europa.eu/idabc/eupl

"""
It contains functions that model the basic mechanics of the engine.

Sub-Modules:

.. currentmodule:: compas.functions.physical.engine

.. autosummary::
    :nosignatures:
    :toctree: engine/

    co2_emission
"""


from math import pi
import numpy as np
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import GradientBoostingRegressor
from scipy.interpolate import InterpolatedUnivariateSpline
from scipy.optimize import fmin
from sklearn.metrics import mean_absolute_error
from compas.functions.physical.constants import *
from compas.functions.physical.utils import bin_split, reject_outliers, \
    clear_gear_fluctuations


def get_full_load(fuel_type):
    """
    Returns vehicle full load curve.

    :param fuel_type:
        Vehicle fuel type (diesel or gasoline).
    :type fuel_type: str

    :return:
        Vehicle normalized full load curve.
    :rtype: InterpolatedUnivariateSpline
    """

    full_load = {
        'gasoline': InterpolatedUnivariateSpline(
            np.linspace(0, 1.2, 13),
            [0.1, 0.198238659, 0.30313392, 0.410104642, 0.516920841,
             0.621300767, 0.723313491, 0.820780368, 0.901750158, 0.962968496,
             0.995867804, 0.953356174, 0.85]),
        'diesel': InterpolatedUnivariateSpline(
            np.linspace(0, 1.2, 13),
            [0.1, 0.278071182, 0.427366185, 0.572340499, 0.683251935,
             0.772776746, 0.846217049, 0.906754984, 0.94977083, 0.981937981,
             1, 0.937598144, 0.85])
    }
    return full_load[fuel_type]


def calculate_full_load(full_load_speeds, full_load_powers, idle_engine_speed):
    """
    Calculates the full load curve.

    :param full_load_speeds:
        T1 map speed vector [RPM].
    :type full_load_speeds: list

    :param full_load_powers: list
        T1 map power vector [kW].
    :type full_load_powers: list

    :param idle_engine_speed:
        Engine speed idle median and std [RPM].
    :type idle_engine_speed: (float, float)

    :return:
        Vehicle full load curve, Maximum power [kW], Rated engine speed [RPM].
    :rtype: (InterpolatedUnivariateSpline, float, float)
    """

    v = list(zip(full_load_powers, full_load_speeds))
    max_engine_power, max_engine_speed_at_max_power = max(v)

    p_norm = np.asarray(full_load_powers) / max_engine_power
    n_norm = (max_engine_speed_at_max_power - idle_engine_speed[0])
    n_norm = (np.asarray(full_load_speeds) - idle_engine_speed[0]) / n_norm

    flc = InterpolatedUnivariateSpline(n_norm, p_norm)

    return flc, max_engine_power, max_engine_speed_at_max_power


def identify_idle_engine_speed_out(velocities, engine_speeds_out):
    """
    Identifies engine speed idle and its standard deviation [RPM].

    :param velocities:
        Velocity vector [km/h].
    :type velocities: np.array

    :param engine_speeds_out:
        Engine speed vector [RPM].
    :type engine_speeds_out: np.array

    :returns:
        Idle engine speed and its standard deviation [RPM].
    :rtype: (float, float)
    """

    b = velocities < VEL_EPS & engine_speeds_out > MIN_ENGINE_SPEED

    x = engine_speeds_out[b]

    idle_speed = bin_split(x, bin_std=(0.01, 0.3))[1][0]

    return idle_speed[-1], idle_speed[1]


def identify_upper_bound_engine_speed(
        gears, engine_speeds_out, idle_engine_speed):
    """
    Identifies upper bound engine speed.

    It is used to correct the gear prediction for constant accelerations (see
    :func:`compas.functions.physical.AT_gear.
    correct_gear_upper_bound_engine_speed`).

    This is evaluated as the median value plus 0.67 standard deviation of the
    filtered cycle engine speed (i.e., the engine speeds when engine speed >
    minimum engine speed plus 0.67 standard deviation and gear < maximum gear).

    :param gears:
        Gear vector [-].
    :type gears: np.array

    :param engine_speeds_out:
         Engine speed vector [RPM].
    :type engine_speeds_out: np.array

    :param idle_engine_speed:
        Idle engine speed and its standard deviation [RPM].
    :type idle_engine_speed: (float, float)

    :returns:
        Upper bound engine speed [RPM].
    :rtype: float

    .. note:: Assuming a normal distribution then about 68 percent of the data
       values are within 0.67 standard deviation of the mean.
    """

    max_gear = max(gears)

    idle_speed = idle_engine_speed[1]

    dom = (engine_speeds_out > idle_speed) & (gears < max_gear)

    m, sd = reject_outliers(engine_speeds_out[dom])

    return m + sd * 0.674490


def calibrate_engine_temperature_regression_model(
        engine_temperatures, gear_box_powers_in, gear_box_speeds_in):
    """
    Calibrates an engine temperature regression model to predict engine
    temperatures.

    This model returns the delta temperature function of temperature (previous),
    acceleration, and power at the wheel.

    :param engine_temperatures:
        Engine temperature vector [°C].
    :type engine_temperatures: np.array

    :param gear_box_powers_in:
        Gear box power vector [kW].
    :type gear_box_powers_in: np.array

    :param gear_box_speeds_in:
        Gear box speed vector [RPM].
    :type gear_box_speeds_in: np.array

    :return:
        The calibrated engine temperature regression model.
    :rtype: sklearn.ensemble.GradientBoostingRegressor
    """

    temp = np.zeros(engine_temperatures.shape)
    temp[1:] = engine_temperatures[:-1]

    kw = {
        'random_state': 0,
        'max_depth': 2,
        'n_estimators': int(min(300, 0.25 * (len(temp) - 1)))
    }

    model = GradientBoostingRegressor(**kw)

    X = list(zip(temp, gear_box_powers_in, gear_box_speeds_in))

    model.fit(X[1:], np.diff(engine_temperatures))

    return model


def predict_engine_temperatures(
        model, gear_box_powers_in, gear_box_speeds_in, initial_temperature):
    """
    Predicts the engine temperature [°C].

    :param model:
        Engine temperature regression model.
    :type model: sklearn.ensemble.GradientBoostingRegressor

    :param gear_box_powers_in:
        Gear box power vector [kW].
    :type gear_box_powers_in: np.array

    :param gear_box_speeds_in:
        Gear box speed vector [RPM].
    :type gear_box_speeds_in: np.array

    :param initial_temperature:
        Engine initial temperature [°C]
    :type initial_temperature: float

    :return:
        Engine temperature vector [°C].
    :rtype: np.array
    """

    predict = model.predict
    it = zip(gear_box_powers_in[:-1], gear_box_speeds_in[:-1])

    temp = [initial_temperature]
    for p, s in it:
        temp.append(temp[-1] + predict([[temp[-1], p, s]])[0])

    return np.array(temp)


def identify_thermostat_engine_temperature(engine_temperatures):
    """
    Identifies thermostat engine temperature and its limits [°C].

    :param engine_temperatures:
        Engine temperature vector [°C].
    :type engine_temperatures: np.array

    :return:
        Thermostat engine temperature [°C].
    :rtype: float
    """

    m, s = reject_outliers(engine_temperatures, n=2)

    max_temp = max(engine_temperatures)

    if max_temp - m > s:
        m = max_temp

    return m


def identify_normalization_engine_temperature(engine_temperatures):
    """
    Identifies normalization engine temperature and its limits [°C].

    :param engine_temperatures:
        Engine temperature vector [°C].
    :type engine_temperatures: np.array

    :return:
        Normalization engine temperature and its limits [°C].
    :rtype: (float, (float, float))
    """

    m, s = reject_outliers(engine_temperatures, n=2)

    max_temp = max(engine_temperatures)
    s = max(s, 20.0)

    return m, (m - s, max_temp)


def identify_initial_engine_temperature(engine_temperatures):
    """
    Identifies initial engine temperature [°C].

    :param engine_temperatures:
        Engine temperature vector [°C].
    :type engine_temperatures: np.array

    :return:
        Initial engine temperature [°C].
    :rtype: float
    """

    return float(engine_temperatures[0])


def calculate_engine_max_torque(
        engine_max_power, engine_max_speed_at_max_power, fuel_type):
    """
    Calculates engine nominal torque [N*m].

    :param engine_max_power:
        Engine nominal power [kW].
    :type engine_max_power: float

    :param engine_max_speed_at_max_power:
        Engine nominal speed at engine nominal power [RPM].
    :type engine_max_speed_at_max_power: float

    :param fuel_type:
        Fuel type (gasoline or diesel).
    :type fuel_type: str

    :return:
        Engine nominal torque [N*m].
    :rtype: float
    """

    c = {
        'gasoline': 1.25,
        'diesel': 1.1
    }[fuel_type]

    return engine_max_power / engine_max_speed_at_max_power * 30000.0 / pi * c


def identify_on_engine(times, engine_speeds_out, idle_engine_speed):
    """
    Identifies if the engine is on and when it starts [-].

    :param times:
        Time vector [s].
    :type times: np.array

    :param engine_speeds_out:
        Engine speed [RPM].
    :type engine_speeds_out: np.array

    :param idle_engine_speed:
        Idle engine speed and its standard deviation [RPM].
    :type idle_engine_speed: (float, float)

    :return:
        If the engine is on and when it starts [-].
    :rtype: (np.array, np.array)
    """

    on_engine = np.zeros(times.shape)

    b = engine_speeds_out > idle_engine_speed[0] - idle_engine_speed[1]
    on_engine[b] = 1

    on_engine = clear_gear_fluctuations(times, on_engine, TIME_WINDOW)

    engine_starts = np.diff(on_engine) > 0

    engine_starts = np.append(engine_starts, False)

    return np.array(on_engine, dtype=bool), engine_starts


def calibrate_start_stop_model(
        on_engine, velocities, accelerations, engine_temperatures):
    """
    Calibrates an start/stop model to predict if the engine is on.

    :param on_engine:
        If the engine is on [-].
    :type on_engine: np.array

    :param velocities:
        Velocity vector [km/h].
    :type velocities: np.array

    :param engine_temperatures:
        Engine temperature vector [°C].
    :type engine_temperatures: np.array

    :return:
        Start/stop model.
    :rtype: sklearn.tree.DecisionTreeClassifier
    """

    model = DecisionTreeClassifier(random_state=0, max_depth=4)

    X = list(zip(velocities, accelerations, engine_temperatures))

    model.fit(X, on_engine)

    return model


def predict_on_engine(
        model, times, velocities, accelerations, engine_temperatures,
        cycle_type, gear_box_type):
    """
    Predicts if the engine is on and when it starts [-].

    :param model:
        Start/stop model.
    :type model: sklearn.tree.DecisionTreeClassifier

    :param times:
        Time vector [s].
    :type times: np.array

    :param velocities:
        Velocity vector [km/h].
    :type velocities: np.array

    :param engine_temperatures:
        Engine temperature vector [°C].
    :type engine_temperatures: np.array

    :return:
        If the engine is on and when it starts [-].
    :rtype: np.array
    """

    X = list(zip(velocities, accelerations, engine_temperatures))

    on_engine = np.array(model.predict(X), dtype=int)

    # legislation imposition
    if cycle_type == 'NEDC' and gear_box_type == 'manual':
        legislation_on_engine = dict.fromkeys(
            [11, 49, 117, 206, 244, 312, 401, 439, 507, 596, 634, 702],
            5.0
        )
        legislation_on_engine[800] = 20.0

        on_engine = np.array(on_engine)

        for k, v in legislation_on_engine.items():
            on_engine[((k - v) <= times) & (times <= k + 3)] = 1

    on_engine = clear_gear_fluctuations(times, on_engine, TIME_WINDOW)

    engine_starts = np.append(np.diff(on_engine) > 0, False)

    return np.array(on_engine, dtype=bool), engine_starts


def calculate_engine_speeds_out(
        gear_box_speeds_in, on_engine, idle_engine_speed, engine_temperatures,
        engine_thermostat_temperature, cold_start_speed_model):
    """
    Calculates the engine speed [RPM].

    :param gear_box_speeds_in:
        Gear box speed [RPM].
    :type gear_box_speeds_in: np.array

    :param on_engine:
        If the engine is on [-].
    :type on_engine: np.array

    :param idle_engine_speed:
        Idle engine speed and its standard deviation [RPM].
    :type idle_engine_speed: (float, float)

    :param engine_temperatures:
        Engine temperature vector [°C].
    :type engine_temperatures: np.array

    :param engine_thermostat_temperature:
        Thermostat engine temperature [°C].
    :type engine_thermostat_temperature: float

    :param cold_start_speed_model:
        Cold start speed model.
    :type cold_start_speed_model: function

    :return:
        Engine speed [RPM].
    :rtype: np.array
    """

    s = gear_box_speeds_in.copy()

    s[np.logical_not(on_engine)] = 0

    s[on_engine & (s < idle_engine_speed[0])] = idle_engine_speed[0]

    add_speeds = cold_start_speed_model(
        s, on_engine, engine_temperatures, engine_thermostat_temperature
    )

    return s + add_speeds


def calibrate_cold_start_speed_model(
        velocities, accelerations, engine_speeds_out, engine_temperatures,
        idle_engine_speed, engine_thermostat_temperature,
        engine_thermostat_temperature_window, gear_box_speeds_in, on_engine):
    """
    Calibrates the cold start speed model.

    :param velocities:
        Velocity vector [km/h].
    :type velocities: np.array

    :param engine_speeds_out:
        Engine speed [RPM].
    :type engine_speeds_out: np.array

    :param engine_temperatures:
        Engine temperature vector [°C].
    :type engine_temperatures: np.array

    :param idle_engine_speed:
        Idle engine speed and its standard deviation [RPM].
    :type idle_engine_speed: (float, float)

    :param engine_thermostat_temperature:
        Thermostat engine temperature [°C].
    :type engine_thermostat_temperature: float

    :return:
        Cold start speed model.
    :rtype: float
    """

    b = engine_temperatures < engine_thermostat_temperature_window[0]
    b &= (velocities < VEL_EPS) & (abs(accelerations) < ACC_EPS)
    b &= (idle_engine_speed[0] < engine_speeds_out)

    t, p = engine_thermostat_temperature, 0.0

    if b.any():
        e_t = engine_thermostat_temperature - engine_temperatures
        p = 1.0 / reject_outliers(e_t[b] / engine_speeds_out[b])[0]

        speeds = calculate_engine_speeds_out(
            gear_box_speeds_in, on_engine, idle_engine_speed,
            engine_temperatures, 0, lambda *args: np.zeros(args[0].shape)
        )

        err_0 = mean_absolute_error(engine_speeds_out[b], speeds[b])

        def error_func(x):
            s_o = (x[0] - engine_temperatures) * x[1]

            b_ = on_engine & (s_o > speeds)

            if not b_.any():
                return err_0

            b_ = np.logical_not(b_)

            s_o[b_] = speeds[b_]

            return mean_absolute_error(engine_speeds_out[b], s_o[b])

        x0 = [t, p]
        res, err = fmin(error_func, x0, disp=False, full_output=True)[0:2]
        t, p = res

        p = float(p) if p > 0.0 and err < err_0 else 0.0

    def model(spd, on_eng, temperatures, *args):
        add_speeds = np.zeros(spd.shape)

        if p > 0:
            s_o = (t - temperatures) * p
            b = on_eng & (s_o > spd)
            add_speeds[b] = s_o[b] - spd[b]

        return add_speeds

    return model


def calibrate_cold_start_speed_model_v1(
        times, velocities, accelerations, engine_speeds_out, idle_engine_speed):

    b = (times < 10) & (engine_speeds_out > idle_engine_speed[0])
    b &= (velocities < VEL_EPS) & (abs(accelerations) < ACC_EPS)

    s = np.mean(engine_speeds_out[b]) if b.any() else idle_engine_speed[0] * 1.2

    if s <= idle_engine_speed[0] * 1.05:
        s = idle_engine_speed[0] * 1.2
    s -= idle_engine_speed[0]

    def model(speeds, on_engine, temperatures, *args):
        add_speeds = np.zeros(speeds.shape)
        b = (temperatures < 30.0) & on_engine
        add_speeds[b] = s * (30.0 - temperatures[b])
        add_speeds[b] /= abs(30.0 - min(temperatures))
        add_speeds += idle_engine_speed[0]
        b = speeds > add_speeds
        add_speeds[b] = 0
        b = np.logical_not(b)
        add_speeds[b] -= speeds[b]
        return add_speeds

    return model


def calculate_engine_powers_out(
        gear_box_powers_in, on_engine, alternator_powers_demand=None, P0=None):
    """
    Calculates the engine power [kW].

    :param gear_box_powers_in:
        Gear box power [kW].
    :type gear_box_powers_in: np.array

    :param on_engine:
        If the engine is on [-].
    :type on_engine: np.array

    :param P0:
        Power engine power threshold limit [kW].
    :type P0: float

    :return:
        Engine power [kW].
    :rtype: np.array
    """

    p = np.zeros(gear_box_powers_in.shape)

    p[on_engine] = gear_box_powers_in[on_engine]
    p[on_engine] += np.abs(alternator_powers_demand[on_engine])

    if P0 is not None:
        p[p < P0] = P0

    return p


def calculate_braking_powers(
        engine_speeds_out, engine_torques_in, friction_powers):
    """
    Calculates braking power [kW].

    :param engine_speeds_out:
        Engine speed [RPM].
    :type engine_speeds_out: np.array

    :param engine_torques_in:
        Engine torque out [N*m].
    :type engine_torques_in: np.array

    :param friction_powers:
        Friction power [kW].
    :type friction_powers: np.array

    :return:
        Braking powers [kW].
    :rtype: np.array
    """

    bp = engine_torques_in * engine_speeds_out * (pi / 30000.0)

    bp[bp < friction_powers] = 0

    return bp


def calculate_friction_powers(
        engine_speeds_out, piston_speeds, engine_loss_parameters,
        engine_capacity):
    """
    Calculates friction power [kW].

    :param engine_speeds_out:
        Engine speed [RPM].
    :type engine_speeds_out: np.array

    :param piston_speeds:
        Piston speed [m/s].
    :type piston_speeds: np.array

    :param engine_loss_parameters:
        Engine parameter (loss, loss2).
    :type engine_loss_parameters: (float, float)

    :param engine_capacity:
        Engine capacity [cm3].
    :type engine_capacity: float

    :return:
        Friction powers [kW].
    :rtype: np.array
    """

    loss, loss2 = engine_loss_parameters
    cap, es = engine_capacity, engine_speeds_out

    # indicative_friction_powers
    return (loss2 * piston_speeds ** 2 + loss) * es * (cap / 1200000.0)


def calculate_mean_piston_speeds(engine_speeds_out, engine_stroke):
    """
    Calculates mean piston speed [m/sec].

    :param engine_speeds_out:
        Engine speed vector [RPM].
    :type engine_speeds_out: np.array

    :param engine_stroke:
        Engine stroke [mm].
    :type engine_stroke: float

    :return:
        Mean piston speed vector [m/s].
    :rtype: np.array, float
    """

    return (engine_stroke / 30000.0) * engine_speeds_out


def calculate_engine_type(fuel_type, engine_is_turbo):
    """
    Calculates the engine type (gasoline turbo, gasoline natural aspiration,
    diesel).

    :param fuel_type:
        Fuel type (gasoline or diesel).
    :type fuel_type: str

    :param engine_is_turbo:
        If the engine is equipped with any kind of charging.
    :type engine_is_turbo: bool

    :return:
        Engine type (gasoline turbo, gasoline natural aspiration, diesel).
    :rtype: str
    """

    engine_type = fuel_type

    if fuel_type == 'gasoline':
        engine_type = 'turbo' if engine_is_turbo else 'natural aspiration'
        engine_type = '%s %s' % (fuel_type, engine_type)

    return engine_type
