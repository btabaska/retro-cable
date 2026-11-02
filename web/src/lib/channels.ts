/** Channel up/down over the dial (ids sorted by channel number), wrapping at the ends. */
export function stepChannel(dial: string[], current: string, delta: 1 | -1): string {
  const index = dial.indexOf(current);
  if (index < 0) return dial[0] ?? current;
  return dial[(index + delta + dial.length) % dial.length] ?? current;
}
