import random
import subprocess


num_of_problems=100

check = True

nurse_count = 2
doctor_count = 1
patient_count = 2
toolset_count = 1

# doctors operate on patients with toolsets
# nurses clean patients and toolsets
# nurses are primary bottleneck
# note, 2 nurses are needed to prepare a patient, so AT LEAST 2 nurses are required in each instance
# at least a third of vars is patients


for x in range(num_of_problems):
    subprocess.run(["python", "instance_generator.py", str(nurse_count), str(doctor_count), str(toolset_count), str(patient_count), str(x)])
    minNurseNum = 2
    minPatientNum = round((x/3))+1
    patient_count = random.randint(minPatientNum, x+1)
    nurse_count = random.randint(minNurseNum, x+3-patient_count)
    doctor_count = random.randint(1, x+4-nurse_count-patient_count)
    toolset_count = random.randint(1, x+6-nurse_count-patient_count-doctor_count)

    print(str(nurse_count)+"+"+str(doctor_count)+"+"+str(patient_count)+"+"+str(toolset_count)+"="+str(nurse_count + patient_count + doctor_count + toolset_count)+" : "+str(x))

