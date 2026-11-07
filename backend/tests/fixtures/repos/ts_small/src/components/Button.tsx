import { memo } from "react";

type Props = { label: string; onPress: () => void };

export const Button = memo(function Button({ label, onPress }: Props) {
  return <button onClick={() => onPress()}>{label}</button>;
});

export function Toolbar() {
  return (
    <div>
      <Button label="Save" onPress={() => save()} />
    </div>
  );
}

function save() {
  console.log("saved");
}
