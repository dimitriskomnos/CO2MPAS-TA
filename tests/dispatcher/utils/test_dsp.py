#!/usr/bin/env python
# -*- coding: UTF-8 -*-
#
# Copyright 2014 European Commission (JRC);
# Licensed under the EUPL (the 'Licence');
# You may not use this work except in compliance with the Licence.
# You may obtain a copy of the Licence at: http://ec.europa.eu/idabc/eupl

import doctest
import unittest

from compas.utils.dsp import *
from compas.dispatcher import Dispatcher
from compas.dispatcher.constants import SINK


class TestDoctest(unittest.TestCase):
    def runTest(self):
        import compas.utils.dsp as utl
        failure_count, test_count = doctest.testmod(
            utl, optionflags=doctest.NORMALIZE_WHITESPACE | doctest.ELLIPSIS)
        self.assertGreater(test_count, 0, (failure_count, test_count))
        self.assertEqual(failure_count, 0, (failure_count, test_count))


class TestDispatcherUtils(unittest.TestCase):
    def test_combine_dicts(self):
        res = combine_dicts({'a': 3, 'c': 3}, {'a': 1, 'b': 2})
        self.assertEqual(res, {'a': 1, 'b': 2, 'c': 3})

    def test_bypass(self):
        self.assertEqual(bypass('a', 'b', 'c'), ('a', 'b', 'c'))
        self.assertEqual(bypass('a'), 'a')

    def test_summation(self):
        self.assertEqual(summation(1, 3.0, 4, 2), 10.0)

    def test_selector(self):
        selector = def_selector(['a', 'b'])
        res = selector({'a': 1, 'b': 1}, {'b': 2, 'c': 3})
        self.assertEqual(res, {'a': 1, 'b': 2})

    def test_replicate(self):
        replicate = def_replicate_value(n=3)
        self.assertEqual(replicate({'a': 3}), [{'a': 3}, {'a': 3}, {'a': 3}])

    def test_replicate_function(self):
        dsp = Dispatcher()

        def fun(a):
            return a + 1, a - 1

        dsp.add_function('fun', ReplicateFunction(fun), ['a', 'b'], ['c', 'd'])

        o = dsp.dispatch(inputs={'a': 3, 'b': 4})[1]

        self.assertEqual(o, {'a': 3, 'b': 4, 'c': (4, 2), 'd': (5, 3)})


class TestSubDispatcher(unittest.TestCase):
    def setUp(self):
        sub_dsp = Dispatcher()

        def fun(a):
            return a + 1, a - 1

        sub_dsp.add_function('fun', fun, ['a'], ['b', 'c'])

        dispatch = SubDispatch(sub_dsp, ['a', 'b', 'c'])
        dispatch_dict = SubDispatch(sub_dsp, ['c'], type_return='dict')
        dispatch_list = SubDispatch(sub_dsp, ['a', 'c'], type_return='list')
        dispatch_val = SubDispatch(sub_dsp, ['c'], type_return='list')

        dsp = Dispatcher()
        dsp.add_function('dispatch', dispatch, ['d'], ['e'])
        dsp.add_function('dispatch_dict', dispatch_dict, ['d'], ['f'])
        dsp.add_function('dispatch_list', dispatch_list, ['d'], ['g'])
        dsp.add_function('dispatch_list', dispatch_val, ['d'], ['h'])
        self.dsp = dsp

    def test_sub_dsp(self):
        from networkx.classes.digraph import DiGraph

        w, o = self.dsp.dispatch(inputs={'d': {'a': 3}})

        self.assertEqual(o['e'], {'a': 3, 'b': 4, 'c': 2})
        self.assertEqual(o['f'], {'c': 2})
        self.assertEqual(o['g'], [3, 2])
        self.assertEqual(o['h'],  2)
        self.assertIsInstance(w.node['dispatch']['workflow'], tuple)
        self.assertIsInstance(w.node['dispatch']['workflow'][0], DiGraph)
        self.assertIsInstance(w.node['dispatch']['workflow'][1], dict)
        self.assertIsInstance(w.node['dispatch']['workflow'][2], dict)


class TestSubDispatchFunction(unittest.TestCase):
    def setUp(self):
        dsp = Dispatcher()
        dsp.add_function(function=max, inputs=['a', 'b'], outputs=['c'])
        dsp.add_function(function=min, inputs=['c', 'b'], outputs=['a'],
                         input_domain=lambda c, b: c * b > 0)
        self.dsp_1 = dsp

        dsp = Dispatcher()

        def f(a, b):
            return a + b, a - b

        dsp.add_function(function=f, inputs=['a', 'b'], outputs=['c', SINK])
        dsp.add_function(function=f, inputs=['c', 'b'], outputs=[SINK, 'd'])
        self.dsp_2 = dsp

    def test_sub_dispatch_function(self):


        fun = SubDispatchFunction(self.dsp_1, 'F', ['a', 'b'], ['a'])
        self.assertEqual(fun.__name__, 'F')

        # noinspection PyCallingNonCallable
        self.assertEqual(fun(2, 1), 1)
        self.assertRaises(ValueError, fun, 3, -1)



        fun = SubDispatchFunction(self.dsp_2, 'F', ['a', 'b'], ['c', 'd'])
        # noinspection PyCallingNonCallable
        self.assertEqual(fun(2, 1), [3, 2])

        self.assertRaises(
            ValueError, SubDispatchFunction, self.dsp_2, 'F', ['a', 'c'], ['d']
        )