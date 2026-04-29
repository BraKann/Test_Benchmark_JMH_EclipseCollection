package benchmark.List;

import org.eclipse.collections.api.list.ImmutableList;
import org.eclipse.collections.api.list.MutableList;
import org.eclipse.collections.api.block.procedure.Procedure;
import org.eclipse.collections.api.list.primitive.MutableIntList;
import org.eclipse.collections.api.list.primitive.MutableLongList;
import org.eclipse.collections.impl.list.mutable.FastList;
import org.eclipse.collections.impl.list.mutable.primitive.IntArrayList;
import org.eclipse.collections.impl.list.mutable.primitive.LongArrayList;
import org.openjdk.jmh.annotations.*;
import org.openjdk.jmh.infra.Blackhole;

import java.util.concurrent.TimeUnit;

@BenchmarkMode(Mode.AverageTime)
@OutputTimeUnit(TimeUnit.MILLISECONDS)
@State(Scope.Thread)
@Warmup(iterations = 2, time = 1)
@Measurement(iterations = 5, time = 1)
@Fork(1)
public class ListBenchmark {

    @Param({"1000", "10000", "100000", "1000000"})
    public int size;

    private FastList<Integer> fastList;
    private ImmutableList<Integer> immutableList;
    private IntArrayList intArrayList;
    private LongArrayList longArrayList;

    @Setup(Level.Trial)
    public void setup() {
        fastList = new FastList<>(size);
        intArrayList = new IntArrayList(size);
        longArrayList = new LongArrayList(size);
        for (int i = 0; i < size; i++) {
            fastList.add(i);
            intArrayList.add(i);
            longArrayList.add(i);
        }
        immutableList = fastList.toImmutable();
    }

    @Benchmark
    public FastList<Integer> insert_fastList() {
        FastList<Integer> list = new FastList<>(size);
        for (int i = 0; i < size; i++) {
            list.add(i);
        }
        return list;
    }

    @Benchmark
    public ImmutableList<Integer> insert_immutableList() {
        FastList<Integer> list = new FastList<>(size);
        for (int i = 0; i < size; i++) {
            list.add(i);
        }
        return list.toImmutable();
    }

    @Benchmark
    public IntArrayList insert_intArrayList() {
        IntArrayList list = new IntArrayList(size);
        for (int i = 0; i < size; i++) {
            list.add(i);
        }
        return list;
    }

    @Benchmark
    public LongArrayList insert_longArrayList() {
        LongArrayList list = new LongArrayList(size);
        for (long i = 0; i < size; i++) {
            list.add(i);
        }
        return list;
    }

    @Benchmark
    public void traverse_fastList_forEach(Blackhole blackhole) {
        fastList.forEach((Procedure<Integer>) each -> blackhole.consume(each));
    }

    @Benchmark
    public void traverse_immutableList_forEach(Blackhole blackhole) {
        immutableList.forEach((Procedure<Integer>) each -> blackhole.consume(each));
    }

    @Benchmark
    public long traverse_fastList_injectInto() {
        return fastList.injectInto(0L, (long acc, Integer value) -> acc + value);
    }

    @Benchmark
    public long traverse_immutableList_injectInto() {
        return immutableList.injectInto(0L, (long acc, Integer value) -> acc + value);
    }

    @Benchmark
    public long traverse_intArrayList_sum() {
        return intArrayList.sum();
    }

    @Benchmark
    public long traverse_longArrayList_sum() {
        return longArrayList.sum();
    }

    @Benchmark
    public boolean search_fastList_contains() {
        return fastList.contains(size / 2);
    }

    @Benchmark
    public boolean search_immutableList_contains() {
        return immutableList.contains(size / 2);
    }

    @Benchmark
    public boolean search_intArrayList_contains() {
        return intArrayList.contains(size / 2);
    }

    @Benchmark
    public int search_fastList_detect() {
        Integer value = fastList.detect(each -> each == size / 2);
        return value == null ? -1 : value;
    }

    @Benchmark
    public int search_immutableList_detect() {
        Integer value = immutableList.detect(each -> each == size / 2);
        return value == null ? -1 : value;
    }

    @Benchmark
    public boolean search_intArrayList_anySatisfy() {
        return intArrayList.anySatisfy(each -> each == size / 2);
    }

    @Benchmark
    public MutableList<Integer> delete_fastList_reject() {
        final int target = size / 2;
        return fastList.reject(each -> each == target);
    }

    @Benchmark
    public ImmutableList<Integer> delete_immutableList_reject() {
        final int target = size / 2;
        return immutableList.reject(each -> each == target);
    }

    @Benchmark
    public MutableIntList delete_intArrayList_reject() {
        final int target = size / 2;
        return intArrayList.reject(each -> each == target);
    }

    @Benchmark
    public MutableLongList delete_longArrayList_reject() {
        final long target = size / 2L;
        return longArrayList.reject(each -> each == target);
    }
}