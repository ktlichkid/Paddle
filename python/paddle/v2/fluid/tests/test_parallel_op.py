import unittest
import paddle.v2.fluid as fluid
import numpy


class BaseParallelForTest(unittest.TestCase):
    def run_test(self, callback, feed, fetch):
        """
        Run the unittest for parallel.for
        Args:
            callback(callable): A callable function returns a generator. There 
                are two yields in the generator function. The first yield 
                returns the data layers, and the second yield returns the loss. 
                The modified data variables will be sent back during the first 
                yield.
            
            feed(dict): The executor feeding dictionary.
            fetch(list|basestr): The fetch name lists. 

        Returns:
            None
            
        Raises:
            AssertionError when the computation of cpu, parallel.for in cpu, 
                gpu, parallel.for in gpu are different.

        """
        cpu = fluid.CPUPlace()
        result_cpu = self._run_test_impl_(
            callback=callback,
            feed=feed,
            fetch=fetch,
            place=cpu,
            use_parallel=False)
        result_cpu_parallel = self._run_test_impl_(
            callback=callback,
            feed=feed,
            fetch=fetch,
            place=cpu,
            use_parallel=True)
        if fluid.core.is_compile_gpu():
            gpu = fluid.CUDAPlace(0)
            result_gpu = self._run_test_impl_(
                callback=callback,
                feed=feed,
                fetch=fetch,
                place=gpu,
                use_parallel=False)
            result_gpu_parallel = self._run_test_impl_(
                callback=callback,
                feed=feed,
                fetch=fetch,
                place=gpu,
                use_parallel=True)
            self._assert_same_(fetch, result_cpu, result_cpu_parallel,
                               result_gpu, result_gpu_parallel)
        else:
            self._assert_same_(fetch, result_cpu, result_cpu_parallel)

    def _run_test_impl_(self, callback, feed, fetch, place, use_parallel=False):
        """
        Run a single test, returns the fetch values
        Args:
            place(Place): the computation place. 
            use_parallel(bool): Whether use parallel.for or not. 

        Returns:
            Fetched numpy arrays.

        """
        if isinstance(fetch, basestring):
            fetch = [fetch]
        main = fluid.Program()
        startup = fluid.Program()
        # Fix seed
        main.random_seed = 10
        startup.random_seed = 10

        with fluid.program_guard(main, startup):
            generator = callback()
            # Automatically insert parallel do if use_parallel = True
            if use_parallel:
                places = fluid.layers.get_places()
                pd = fluid.layers.ParallelDo(places)
                data = next(generator)

                if isinstance(data, fluid.Variable):
                    data = [data]

                with pd.do():
                    ins = map(pd.read_input, data)
                    if len(ins) == 1:
                        ins = ins[0]
                    loss = generator.send(ins)  # patch input
                    pd.write_output(loss)

                loss = pd()
            else:
                data = next(generator)
                loss = generator.send(data)
            self.assertIsNotNone(loss)
            avg_loss = fluid.layers.mean(x=loss)
            fluid.backward.append_backward(loss=avg_loss)

        exe = fluid.Executor(place)
        exe.run(startup)
        return exe.run(main, feed=feed, fetch_list=fetch)

    def _assert_same_(self, fetch, *args):
        """
        Assert the return values of `run_test` are same.
        Args:
            fetch: Fetch list. Used for print error message
            *args: The fetch result lists of each situations.

        Returns:
            None
            
        Raises:
            AssertionError

        """

        def _impl_(a, b, fetch_id, item_id):
            item_str = ['CPU', 'ParallelCPU', 'GPU', 'ParallelGPU']
            flag = numpy.allclose(a, b, rtol=0.1)
            self.assertTrue(flag, "The {0} are different in {1}".format(
                fetch[fetch_id], item_str[item_id]))

        for i, items in enumerate(zip(*args)):
            self.assertGreater(len(items), 0)
            for j in range(1, len(items)):
                _impl_(items[0], items[j], fetch_id=i, item_id=j)


class ParallelOpTest(BaseParallelForTest):
    def test_simple_fc(self):
        def __network__():
            x = fluid.layers.data(shape=[784], dtype='float32', name='img')
            # FIXME: This is a bug of parallel.do
            x.stop_gradient = False
            x = yield x
            hidden = fluid.layers.fc(input=x, size=200, param_attr='fc1.w')
            loss = fluid.layers.mean(x=hidden)
            yield loss

        self.run_test(
            callback=__network__,
            feed={
                'img':
                numpy.random.random(size=(128 * 3, 784)).astype('float32')
            },
            fetch='fc1.w@GRAD')


if __name__ == '__main__':
    unittest.main()