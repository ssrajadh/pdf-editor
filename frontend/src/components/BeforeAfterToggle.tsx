interface Props {
  showOriginal: boolean;
  onToggle: (showOriginal: boolean) => void;
}

export default function BeforeAfterToggle({ showOriginal, onToggle }: Props) {
  return (
    <div className="inline-flex rounded-lg bg-gray-200 p-0.5 text-sm">
      <button
        onClick={() => onToggle(true)}
        className={`px-3 py-1 rounded-md transition-colors font-medium ${
          showOriginal
            ? "bg-white text-gray-900 shadow-sm"
            : "text-gray-500 hover:text-gray-700"
        }`}
      >
        Original
      </button>
      <button
        onClick={() => onToggle(false)}
        className={`px-3 py-1 rounded-md transition-colors font-medium ${
          !showOriginal
            ? "bg-white text-gray-900 shadow-sm"
            : "text-gray-500 hover:text-gray-700"
        }`}
      >
        Edited
      </button>
    </div>
  );
}
