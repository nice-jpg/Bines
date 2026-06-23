package nice.auther.shadow;

import java.util.ArrayList;
import java.util.List;

final class H264AnnexB {
    private H264AnnexB() {
    }

    static List<byte[]> splitNalUnits(byte[] data, int offset, int length) {
        List<byte[]> avccUnits = splitAvccNalUnits(data, offset, length);
        if (!avccUnits.isEmpty()) {
            return avccUnits;
        }
        List<byte[]> units = new ArrayList<>();
        int end = offset + length;
        int start = findStartCode(data, offset, end);
        while (start >= 0) {
            int nalStart = start + startCodeLength(data, start, end);
            int next = findStartCode(data, nalStart, end);
            int nalEnd = next >= 0 ? next : end;
            while (nalEnd > nalStart && data[nalEnd - 1] == 0) {
                nalEnd--;
            }
            if (nalEnd > nalStart) {
                byte[] unit = new byte[nalEnd - nalStart];
                System.arraycopy(data, nalStart, unit, 0, unit.length);
                units.add(unit);
            }
            start = next;
        }
        if (units.isEmpty() && length > 0) {
            byte[] unit = new byte[length];
            System.arraycopy(data, offset, unit, 0, length);
            units.add(unit);
        }
        return units;
    }

    static byte[] concat(byte[] first, byte[] second) {
        if (first == null || first.length == 0) {
            return second;
        }
        byte[] output = new byte[first.length + second.length];
        System.arraycopy(first, 0, output, 0, first.length);
        System.arraycopy(second, 0, output, first.length, second.length);
        return output;
    }

    private static List<byte[]> splitAvccNalUnits(byte[] data, int offset, int length) {
        List<byte[]> units = new ArrayList<>();
        int position = offset;
        int end = offset + length;
        while (position + 4 <= end) {
            int nalLength = ((data[position] & 0xff) << 24)
                    | ((data[position + 1] & 0xff) << 16)
                    | ((data[position + 2] & 0xff) << 8)
                    | (data[position + 3] & 0xff);
            position += 4;
            if (nalLength <= 0 || position + nalLength > end) {
                return new ArrayList<>();
            }
            byte[] unit = new byte[nalLength];
            System.arraycopy(data, position, unit, 0, nalLength);
            units.add(unit);
            position += nalLength;
        }
        if (position != end) {
            return new ArrayList<>();
        }
        return units;
    }

    private static int findStartCode(byte[] data, int offset, int end) {
        for (int i = offset; i + 3 < end; i++) {
            if (data[i] == 0 && data[i + 1] == 0 && data[i + 2] == 1) {
                return i;
            }
            if (i + 4 < end && data[i] == 0 && data[i + 1] == 0 && data[i + 2] == 0 && data[i + 3] == 1) {
                return i;
            }
        }
        return -1;
    }

    private static int startCodeLength(byte[] data, int offset, int end) {
        if (offset + 3 < end && data[offset] == 0 && data[offset + 1] == 0 && data[offset + 2] == 1) {
            return 3;
        }
        return 4;
    }
}
