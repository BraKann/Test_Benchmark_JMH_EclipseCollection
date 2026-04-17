package benchmark;

import org.openjdk.jmh.annotations.*;
import org.eclipse.collections.impl.list.mutable.FastList;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;

@BenchmarkMode(Mode.AverageTime)
@OutputTimeUnit(TimeUnit.MILLISECONDS)
@State(Scope.Thread)
public class CollectionBenchmark {

    private List<Integer> javaList;
    private FastList<Integer> eclipseList;

    @Setup(Level.Trial)
    public void setup() {
        javaList = new ArrayList<>();
        eclipseList = new FastList<>();

        for (int i = 0; i < 100000; i++) {
            javaList.add(i);
            eclipseList.add(i);
        }
    }

    @Benchmark
    public int sumJavaList() {
        int sum = 0;
        for (Integer i : javaList) {
            sum += i;
        }
        return sum;
    }

    @Benchmark
    public int sumEclipseList() {
        int sum = 0;
        for (Integer i : eclipseList) {
            sum += i;
        }
        return sum;
    }
}